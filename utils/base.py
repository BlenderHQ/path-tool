from enum import Enum

import bpy
import bmesh

from . import unified_path
from . import draw

if "_rc" in locals():
    import importlib
    importlib.reload(unified_path)

_rc = None

Path = unified_path.Path


class InteractEvent(Enum):
    """Control element interaction mode"""
    ADD = 1
    ADD_NEW_PATH = 2
    REMOVE = 3
    DRAG = 6
    CLOSE = 7
    CHDIR = 8
    RELEASE = 9


class PathUtils:
    @property
    def active_path(self):
        if (self._active_path_index is not None) and (self._active_path_index <= len(self.path_seq) - 1):
            return self.path_seq[self._active_path_index]

    @active_path.setter
    def active_path(self, value: Path):
        if value not in self.path_seq:
            self.path_seq.append(value)
        self._active_path_index = self.path_seq.index(value)

    @staticmethod
    def set_selection_state(elem_seq, state=True):
        for elem in elem_seq:
            elem.select = state

    def get_selected_elements(self, mesh_elements):
        selected_elements = []
        for _, bm in self.bm_seq:
            selected_elements.extend([n for n in getattr(bm, mesh_elements) if n.select])
        return selected_elements

    def get_element_by_mouse(self, context, event):
        """Methon for element selection by mouse.
        For edges are selected verts (they used as control elements), for faces selected faces
        Return's tuple (BMElement, bpy.types.Object.matrix_world)"""
        tool_settings = context.scene.tool_settings

        initial_select_mode = tuple(tool_settings.mesh_select_mode)
        if initial_select_mode[1]:  # Change select mode for edges path (select verts)
            tool_settings.mesh_select_mode = (True, False, False)

        bpy.ops.mesh.select_all(action='DESELECT')

        mouse_location = (event.mouse_region_x, event.mouse_region_y)
        bpy.ops.view3d.select(location=mouse_location)

        elem = None
        matrix_world = None

        for ob, bm in self.bm_seq:
            elem = bm.select_history.active
            if elem:
                matrix_world = ob.matrix_world
                break
        tool_settings.mesh_select_mode = initial_select_mode
        # bpy.ops.mesh.select_all(action='DESELECT')  # ---------
        return elem, matrix_world

    def get_linked_island_index(self, context, elem):
        for i, linked_island in enumerate(self.mesh_islands):
            if elem in linked_island:
                return i

        tool_settings = context.scene.tool_settings
        initial_select_mode = tuple(tool_settings.mesh_select_mode)
        mesh_elements = "faces"
        if initial_select_mode[1]:  # Change select mode for edges path (select verts)
            mesh_elements = "verts"
            tool_settings.mesh_select_mode = (True, False, False)

        bpy.ops.mesh.select_all(action='DESELECT')
        elem.select_set(True)
        bpy.ops.mesh.select_linked(delimit={'NORMAL'})
        linked_island = self.get_selected_elements(mesh_elements)
        tool_settings.mesh_select_mode = initial_select_mode
        bpy.ops.mesh.select_all(action='DESELECT')
        self.mesh_islands.append(linked_island)
        return len(self.mesh_islands) - 1

    def update_meshes(self, context):
        for ob, bm in self.bm_seq:
            bm.select_flush_mode()
        for ob in context.objects_in_mode:
            bmesh.update_edit_mesh(ob.data, False, False)

    def update_path_beetween(self, context, elem_0, elem_1):
        tool_settings = context.scene.tool_settings
        initial_select_mode = tuple(tool_settings.mesh_select_mode)
        mesh_elements = "faces"
        if initial_select_mode[1]:  # Change select mode for edges path (select verts)
            mesh_elements = "edges"
            tool_settings.mesh_select_mode = (True, False, False)

        bpy.ops.mesh.select_all(action='DESELECT')
        self.set_selection_state((elem_0, elem_1), True)
        bpy.ops.mesh.shortest_path_select()
        self.set_selection_state((elem_0, elem_1), False)
        fill_seq = self.get_selected_elements(mesh_elements)
        bpy.ops.mesh.select_all(action='DESELECT')
        # Exception if control points in one edge
        if (not fill_seq) and initial_select_mode[1]:
            for edge in elem_0.link_edges:
                if edge.other_vert(elem_0) == elem_1:
                    fill_seq = [edge]
        tool_settings.mesh_select_mode = initial_select_mode
        return fill_seq

    def update_fills_by_element_index(self, context, elem_index):
        pairs_items = self.active_path.get_pairs_items(elem_index)
        for item in pairs_items:
            elem_0, elem_1, fill_index = item
            fill_seq = self.update_path_beetween(context, elem_0, elem_1)

            self.active_path.fill_elements[fill_index] = fill_seq
            batch = draw.gen_batch_fill_elements(context, fill_seq)
            self.active_path.batch_seq_fills[fill_index] = batch

    def gen_final_elements_seq(self, context):
        tool_settings = context.scene.tool_settings
        select_mode = tuple(tool_settings.mesh_select_mode)
        self.final_elements_select_only_seq = []
        self.final_elements_markup_seq = []
        for path in self.path_seq:
            if select_mode[1]:
                for fill_seq in path.fill_elements:
                    self.final_elements_select_only_seq.extend(fill_seq)
                    self.final_elements_markup_seq.extend(fill_seq)
            # For face selection mode control elements are required too
            if select_mode[2]:
                for fill_seq in path.fill_elements:
                    self.final_elements_select_only_seq.extend(fill_seq)
                self.final_elements_select_only_seq.extend(path.control_elements)
                for face in self.final_elements_select_only_seq:
                    self.final_elements_markup_seq.extend(face.edges)

        # Remove duplicates
        self.final_elements_select_only_seq = list(dict.fromkeys(self.final_elements_select_only_seq))
        self.final_elements_markup_seq = list(dict.fromkeys(self.final_elements_markup_seq))

    def interact_control_element(self, context, elem, matrix_world, interact_event):
        """Main method of interacting with all pathes"""
        if interact_event is InteractEvent.ADD:
            # Only the first click
            if not self.path_seq:
                self.interact_control_element(context, elem, matrix_world, InteractEvent.ADD_NEW_PATH)
                return

            new_elem_index = None

            elem_index = self.active_path.is_in_control_elements(elem)
            if elem_index is None:
                new_elem_index = len(self.active_path.control_elements)

                fill_index = self.active_path.is_in_fill_elements(elem)
                if fill_index is None:
                    is_found_in_other_path = False
                    for path in self.path_seq:
                        if path == self.active_path:
                            continue
                        other_elem_index = path.is_in_control_elements(elem)
                        if other_elem_index is None:
                            other_fill_index = path.is_in_fill_elements(elem)
                            if other_fill_index is not None:
                                is_found_in_other_path = True
                        else:
                            is_found_in_other_path = True

                        if is_found_in_other_path:
                            self.active_path = path
                            self._just_closed_path = False
                            self.interact_control_element(context, elem, matrix_world, InteractEvent.ADD)
                            return
                else:
                    new_elem_index = fill_index + 1
                    self._just_closed_path = False

            elif len(self.active_path.control_elements) == 1:
                batch = draw.gen_batch_control_elements(context, self.active_path)  # Draw
                self.active_path.batch_control_elements = batch

            if elem_index is not None:
                self.drag_elem_index = elem_index
                self._just_closed_path = False
            self._drag_elem = elem

            if self._just_closed_path:
                self.interact_control_element(context, elem, matrix_world, InteractEvent.ADD_NEW_PATH)
                return

            if new_elem_index is not None:
                self.drag_elem_index = new_elem_index
                # Add a new control element to active path
                linked_island_index = self.get_linked_island_index(context, elem)
                if self.active_path.island_index != linked_island_index:
                    self.interact_control_element(context, elem, matrix_world, InteractEvent.ADD_NEW_PATH)
                    return

                self.active_path.insert_control_element(new_elem_index, elem)
                self.update_fills_by_element_index(context, new_elem_index)

                batch = draw.gen_batch_control_elements(context, self.active_path)  # Draw
                self.active_path.batch_control_elements = batch

        elif interact_event is InteractEvent.ADD_NEW_PATH:
            # Adding new path
            linked_island_index = self.get_linked_island_index(context, elem)
            self.active_path = Path(elem, linked_island_index, matrix_world)
            # Recursion used to add new control element to newly created path
            self._just_closed_path = False
            self.interact_control_element(context, elem, matrix_world, InteractEvent.ADD)
            self.report(type={'INFO'}, message="Created new path")
            return

        elif interact_event is InteractEvent.REMOVE:
            # Remove control element
            self._just_closed_path = False

            elem_index = self.active_path.is_in_control_elements(elem)
            if elem_index is None:
                for path in self.path_seq:
                    other_elem_index = path.is_in_control_elements(elem)
                    if other_elem_index is not None:
                        self.active_path = path
                        self.interact_control_element(context, elem, matrix_world, InteractEvent.REMOVE)
                        return
            else:
                self.active_path.pop_control_element(elem_index)

                # Remove the last control element from path
                if not len(self.active_path.control_elements):
                    self.path_seq.remove(self.active_path)
                    if len(self.path_seq):
                        self.active_path = self.path_seq[-1]
                else:
                    self.update_fills_by_element_index(context, elem_index)
                    batch = draw.gen_batch_control_elements(context, self.active_path)  # Draw
                    self.active_path.batch_control_elements = batch

        elif interact_event is InteractEvent.DRAG:
            # Drag control element
            if not self._drag_elem:
                return
            self._just_closed_path = False

            linked_island_index = self.get_linked_island_index(context, elem)
            if self.active_path.island_index == linked_island_index:
                self.active_path.control_elements[self.drag_elem_index] = elem
                self._drag_elem = elem
                self.update_fills_by_element_index(context, self.drag_elem_index)
                batch = draw.gen_batch_control_elements(context, self.active_path)  # Draw
                self.active_path.batch_control_elements = batch

        # Switch active path direction
        elif interact_event is InteractEvent.CHDIR:
            self.active_path.reverse()
            self._just_closed_path = False

        # Close active path
        elif interact_event is InteractEvent.CLOSE:
            self.active_path.close = not self.active_path.close

            if self.active_path.close:
                self.update_fills_by_element_index(context, 0)
                if len(self.active_path.control_elements) > 2:
                    self._just_closed_path = True
            else:
                self.active_path.fill_elements[-1] = []
                self.active_path.batch_seq_fills[-1] = None
                self._just_closed_path = False

        # Release interact event event
        elif interact_event is InteractEvent.RELEASE:
            self.drag_elem_index = None
            self._drag_elem = None

            if self.view_center_pick:
                bpy.ops.view3d.view_center_pick('INVOKE_DEFAULT')

            # # Check and handle duplicated control elements
            non_doubles = []

            check_list = [self.active_path]
            check_list.extend([n for n in self.path_seq if n != self.active_path])

            control_elements = self.active_path.control_elements

            for i, control_element in enumerate(control_elements):
                if control_element in non_doubles:
                    continue
                for other_path in check_list:
                    doubles_count = other_path.control_elements.count(control_element)

                    if other_path == self.active_path:
                        # Double same path
                        if doubles_count > 1:
                            for j, other_control_element in enumerate(control_elements):
                                if i == j:  # Skip current control element
                                    continue
                                if other_control_element == control_element:
                                    # First-last control element same path
                                    if i == 0 and j == len(control_elements) - 1:
                                        self.active_path.pop_control_element(-1)
                                        self.interact_control_element(
                                            context, elem, matrix_world, InteractEvent.CLOSE)
                                        self.report(type={'INFO'}, message="Closed active path")
                                    elif i in (j - 1, j + 1):
                                        self.active_path.pop_control_element(j)
                                        batch = draw.gen_batch_control_elements(context, self.active_path)  # Draw
                                        self.active_path.batch_control_elements = batch
                                        self.report(type={'INFO'}, message="Merged adjacent control elements")
                                    else:
                                        pass

                                    return
                    # Double control element in another path
                    elif doubles_count >= 1:
                        for j, other_control_element in enumerate(other_path.control_elements):
                            if other_control_element == control_element:
                                # Endpoint control element different path
                                if (
                                    (not self.active_path.close) and
                                    (not other_path.close) and
                                    (i in (0, len(self.active_path.control_elements) - 1)) and
                                        (j in (0, len(other_path.control_elements) - 1))):

                                    self.active_path += other_path
                                    _path = self.active_path
                                    self.path_seq.remove(other_path)
                                    self.active_path = _path

                                    batch = draw.gen_batch_control_elements(context, self.active_path)  # Draw
                                    self.active_path.batch_control_elements = batch
                                    self.report(type={'INFO'}, message="Joined two paths")
                                    return
                    else:
                        non_doubles.append(control_element)
        # print(self.active_path)
