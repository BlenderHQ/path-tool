from collections import deque

import bpy
import bmesh

from . import utils

if "_rc" in locals():
    import importlib

    importlib.reload(utils)

_rc = None

InteractEvent = utils.base.InteractEvent


class MESH_OT_select_path(utils.base.PathUtils, bpy.types.Operator):
    """
    Operator class only handle events.
    Methods of path creation and interaction with mesh elements exist inside parent PathUtils class.
    """
    bl_idname = "view3d.select_path"
    bl_label = "Select Path"
    bl_description = "Tool for selecting and marking up mesh object elements"

    bl_options = {'REGISTER', 'UNDO'}

    # Operator properties
    context_action: utils.props.context_action
    context_undo: utils.props.context_undo
    mark_select: utils.props.mark_select
    mark_seam: utils.props.mark_seam
    mark_sharp: utils.props.mark_sharp

    # UI draw methods
    draw = utils.ui.operator_draw
    popup_menu_pie_draw = utils.ui.popup_menu_pie_draw

    def invoke(self, context, event):
        wm = context.window_manager

        # Stadard input event keys (type, value, alt, ctrl, shift)
        kc = wm.keyconfigs.user
        self.mouse_buttons = utils.inputs.get_mouse_buttons(wm)
        self.navigation_evkeys = utils.inputs.get_navigation_evkeys(kc)
        self.modal_action_evkeys = utils.inputs.get_modal_action_evkeys(kc)
        self.undo_redo_evkeys = utils.inputs.get_undo_redo_evkeys(kc)
        self.use_rotate_around_active = context.preferences.inputs.use_rotate_around_active

        # Setup mesh select mode
        tool_settings = context.scene.tool_settings
        initial_select_mode = tuple(tool_settings.mesh_select_mode)
        mesh_mode = (False, True, False)
        header_text_mode = "Edge Selection Mode"
        if initial_select_mode[2]:
            mesh_mode = (False, False, True)
            header_text_mode = "Face Selection Mode"
        tool_settings.mesh_select_mode = mesh_mode

        # Bmesh (bpy.types.Object - bmesh.Bmesh) pairs
        self.bm_seq = []
        for ob in context.objects_in_mode:
            self.bm_seq.append((ob, bmesh.from_edit_mesh(ob.data)))

        if initial_select_mode[0]:
            mesh_elements = "verts"
        if initial_select_mode[1]:
            mesh_elements = "edges"
        else:
            mesh_elements = "faces"
        self.initial_select = self.get_selected_elements(mesh_elements)
        self.draw_handle_3d = bpy.types.SpaceView3D.draw_handler_add(
            utils.draw.draw_callback_3d, (self,), 'WINDOW', 'POST_VIEW')
        # Prevent first click empty space
        elem, _ = self.get_element_by_mouse(context, event)
        if not elem:
            tool_settings.mesh_select_mode = initial_select_mode
            self.cancel(context)
            return {'CANCELLED'}
        #
        self.navigation_element = elem

        self.is_mouse_pressed = False
        self.is_navigation_active = False
        #
        context.area.header_text_set("Path Tool (%s)" % header_text_mode)
        wm.modal_handler_add(self)

        self.path_seq = []
        self.mesh_islands = []
        self.drag_elem_indices = []

        self._active_path_index = None
        self._drag_elem = None
        self._just_closed_path = False

        undo_steps = context.preferences.edit.undo_steps
        self.undo_history = deque(maxlen=undo_steps)
        self.redo_history = deque(maxlen=undo_steps)

        self.final_elements_select_only_seq = []
        self.final_elements_markup_seq = []

        self.modal(context, event)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_3d, 'WINDOW')
        self.set_selection_state(self.initial_select, True)
        self.update_meshes(context)
        context.area.header_text_set(None)

    def modal(self, context, event):
        evkey = utils.inputs.get_evkey(event)
        select_mb, context_mb = self.mouse_buttons
        modal_action = self.modal_action_evkeys.get(evkey, None)
        undo_redo_action = self.undo_redo_evkeys.get(evkey, None)
        interact_event = None

        # Navigation
        if evkey in self.navigation_evkeys:
            if self.use_rotate_around_active:
                bpy.ops.mesh.select_all(action='DESELECT')
                self.navigation_element.select_set(True)
                self.is_navigation_active = True
            return {'PASS_THROUGH'}

        elif self.is_navigation_active and event.value == 'RELEASE':
            self.is_navigation_active = False
            self.set_selection_state(self.initial_select, True)
            self.update_meshes(context)
            return {'RUNNING_MODAL'}

        # Cancel
        elif modal_action == 'CANCEL':
            self.cancel(context)
            return {'CANCELLED'}

        # Apply all
        elif (modal_action == 'APPLY') or ('APPLY' in self.context_action):
            self.context_action = set()

            self.gen_final_elements_seq(context)

            context.area.header_text_set(None)
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_3d, 'WINDOW')
            return self.execute(context)

        # Close path
        elif 'TCLPATH' in self.context_action:
            self.context_action = set()
            interact_event = InteractEvent.CLOSE

        # Switch direction
        elif 'CHDIR' in self.context_action:
            self.context_action = set()
            interact_event = InteractEvent.CHDIR

        # Undo
        elif (undo_redo_action == 'UNDO') or ('UNDO' in self.context_undo):
            self.context_undo = set()
            return utils.redo.undo(self, context)

        # Redo
        elif (undo_redo_action == 'REDO') or ('REDO' in self.context_undo):
            self.context_undo = set()
            utils.redo.redo(self, context)

        # Open context pie menu
        elif evkey == (context_mb, 'PRESS', False, False, False):  # Context menu mouse button
            wm = context.window_manager
            wm.popup_menu_pie(event=event, draw_func=self.popup_menu_pie_draw, title='Path Tool', icon='NONE')

        # Select mouse button
        elif evkey == (select_mb, 'PRESS', False, False, False):
            self.is_mouse_pressed = True
            interact_event = InteractEvent.ADD

        # Select mouse button + Shift
        elif evkey == (select_mb, 'PRESS', False, False, True):
            self.is_mouse_pressed = True
            interact_event = InteractEvent.ADD_NEW_PATH

        # Select mouse button + Ctrl
        elif evkey == (select_mb, 'PRESS', False, True, False):
            self.is_mouse_pressed = False
            interact_event = InteractEvent.REMOVE

        # Release select mouse event
        elif evkey in ((select_mb, 'RELEASE', False, False, False),
                       (select_mb, 'RELEASE', False, True, False),
                       (select_mb, 'RELEASE', False, False, True),
                       ):
            self.is_mouse_pressed = False
            interact_event = InteractEvent.RELEASE

        if self.is_mouse_pressed:
            if evkey[0] == 'MOUSEMOVE':
                interact_event = InteractEvent.DRAG

        if interact_event is not None:
            elem, matrix_world = self.get_element_by_mouse(context, event)
            if elem:
                self.navigation_element = elem
            self.interact_control_element(context, elem, matrix_world, interact_event)

            self.set_selection_state(self.initial_select, True)
            self.update_meshes(context)

        # If removed the last control element of the last path
        if not len(self.path_seq):
            self.cancel(context)
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def execute(self, context):
        select_only_seq = self.final_elements_select_only_seq

        if self.mark_select != 'NONE':
            if self.mark_select == 'EXTEND':
                for elem in select_only_seq:
                    elem.select_set(True)
            elif self.mark_select == 'SUBTRACT':
                for elem in select_only_seq:
                    elem.select_set(False)
            elif self.mark_select == 'INVERT':
                for elem in select_only_seq:
                    elem.select_set(not elem.select)

        markup_seq = self.final_elements_markup_seq

        if self.mark_seam != 'NONE':
            if self.mark_seam == 'MARK':
                for elem in markup_seq:
                    elem.seam = True
            elif self.mark_seam == 'CLEAR':
                for elem in markup_seq:
                    elem.seam = False
            elif self.mark_seam == 'TOGGLE':
                for elem in markup_seq:
                    elem.seam = not elem.seam

        if self.mark_sharp != 'NONE':
            if self.mark_sharp == 'MARK':
                for elem in markup_seq:
                    elem.smooth = False
            elif self.mark_sharp == 'CLEAR':
                for elem in markup_seq:
                    elem.smooth = True
            elif self.mark_sharp == 'TOGGLE':
                for elem in markup_seq:
                    elem.smooth = not elem.smooth

        self.update_meshes(context)

        return {'FINISHED'}
