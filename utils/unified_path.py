import bmesh


class Path:
    """
    Clear python container for elements in single path.
    Structure:

     [0]        [1]        [2]        [3]        [n]
      |  \\      |  \\      |  \\      |  \\      |
    ce_0 [...] ce_1 [...] ce_2 [...] ce_3 [...] ce_n   [...]
           |          |          |          |            |
        (fill_0)  (fill_1)   (fill_2)   (fill_3)   (fill_close)
           |          |          |          |            |
        (fba_0)    (fba_0)    (fba_0)    (fba_0)    (fba_close)

    Note:
        If elem parameter passed at instance initialization,
        will be added placeholders to fill_elements and batch_seq_fills.
    """

    __slots__ = (
        "island_index",
        "ob",
        "batch_control_elements",
        "control_elements",
        "fill_elements",
        "batch_seq_fills",
        "close",
        "direction",
    )

    def __init__(self, elem=None, linked_island_index=0, ob=None):
        # Index of mesh elements island in operator
        self.island_index = linked_island_index
        # Model matrix of object on which path is
        self.ob = ob
        # One batch for all control elements
        self.batch_control_elements = None

        self.control_elements = []
        self.fill_elements = []
        # And separate batches for each fill seq. self.batch_seq_fills[-1] reserved for
        self.batch_seq_fills = []

        if elem is not None:
            self.control_elements.append(elem)
            # Placeholders for fill seq and it's batch from first to the last control element(if path.close)
            self.fill_elements.append([])
            self.batch_seq_fills.append(None)

        self.close = False
        self.direction = True

    def copy(self):
        new_path = Path()
        new_path.control_elements = self.control_elements.copy()
        new_path.fill_elements = self.fill_elements.copy()
        new_path.batch_seq_fills = self.batch_seq_fills.copy()

        new_path.batch_control_elements = self.batch_control_elements
        new_path.island_index = self.island_index
        new_path.ob = self.ob
        new_path.close = self.close
        new_path.direction = self.direction

        return new_path

    def __repr__(self):
        # For development purposes only
        batch_seq_fills_formatted = []
        for i, batch in enumerate(self.batch_seq_fills):
            if batch:
                batch_seq_fills_formatted.append("fb_%d" % i)
                continue
            batch_seq_fills_formatted.append(batch)

        ["fb_%d" % i for i in range(len(self.batch_seq_fills))]
        return "\nPath[%d]:\n    ce: %s\n    fe: %s\n    fb: %s" % (
            id(self),
            str([n.index for n in self.control_elements]),
            str([len(n) for n in self.fill_elements]),
            str(batch_seq_fills_formatted)
        )

    def __add__(self, other):
        assert self.island_index == other.island_index

        is_found_merged_elements = False
        for i in (0, -1):
            elem = self.control_elements[i]
            for j in (0, -1):
                other_elem = other.control_elements[j]
                if elem == other_elem:
                    is_found_merged_elements = True

                    if i == -1 and j == 0:
                        # End-First
                        self.control_elements.pop(-1)
                        self.fill_elements.pop(-1)
                        self.batch_seq_fills.pop(-1)

                        self.control_elements.extend(other.control_elements)
                        self.fill_elements.extend(other.fill_elements)
                        self.batch_seq_fills.extend(other.batch_seq_fills)

                    elif i == 0 and j == -1:
                        # First-End

                        self.control_elements.pop(0)

                        other.fill_elements.pop(-1)
                        other.batch_seq_fills.pop(-1)

                        other.control_elements.extend(self.control_elements)
                        other.fill_elements.extend(self.fill_elements)
                        other.batch_seq_fills.extend(self.batch_seq_fills)

                        self.control_elements = other.control_elements
                        self.fill_elements = other.fill_elements
                        self.batch_seq_fills = other.batch_seq_fills

                    elif i == 0 and j == 0:
                        # First-First
                        self.control_elements.pop(0)

                        other.control_elements.reverse()
                        other.fill_elements.reverse()
                        other.batch_seq_fills.reverse()

                        other.fill_elements.pop(0)
                        other.batch_seq_fills.pop(0)

                        other.control_elements.extend(self.control_elements)
                        other.fill_elements.extend(self.fill_elements)
                        other.batch_seq_fills.extend(self.batch_seq_fills)

                        self.control_elements = other.control_elements
                        self.fill_elements = other.fill_elements
                        self.batch_seq_fills = other.batch_seq_fills

                    elif i == -1 and j == -1:
                        # End-End
                        other.reverse()
                        self.control_elements.pop(-1)
                        self.fill_elements.pop(-1)
                        self.batch_seq_fills.pop(-1)

                        self.control_elements.extend(other.control_elements)
                        self.fill_elements.extend(other.fill_elements)
                        self.batch_seq_fills.extend(other.batch_seq_fills)

            if is_found_merged_elements:
                break

        return self

    def reverse(self):
        self.control_elements.reverse()
        close_path_fill = self.fill_elements.pop(-1)
        close_path_batch = self.batch_seq_fills.pop(-1)
        self.fill_elements.reverse()
        self.batch_seq_fills.reverse()
        self.fill_elements.append(close_path_fill)
        self.batch_seq_fills.append(close_path_batch)
        self.direction = not self.direction
        return self

    def is_in_control_elements(self, elem):
        """
        Return's element index in self.control_elements if exist, otherwise None
        """
        if elem in self.control_elements:
            return self.control_elements.index(elem)

    def is_in_fill_elements(self, elem):
        """
        Return's index of fill in self.fill_elements if element exist in any fill, otherwise None
        """
        for fill_index, fill_seq in enumerate(self.fill_elements):
            if isinstance(elem, bmesh.types.BMVert):
                for edge in fill_seq:
                    for vert in edge.verts:
                        if elem == vert:
                            return fill_index
            elif isinstance(elem, bmesh.types.BMFace):
                if elem in fill_seq:
                    return fill_index

    def insert_control_element(self, elem_index, elem):
        """
        Insert
        - new control element
        - empty list for fill elements after this element
        - placeholder for fill batch
        """
        self.control_elements.insert(elem_index, elem)
        self.fill_elements.insert(elem_index, [])
        self.batch_seq_fills.insert(elem_index, None)

    def remove_control_element(self, elem):
        elem_index = self.control_elements.index(elem)
        self.pop_control_element(elem_index)

    def pop_control_element(self, elem_index):
        elem = self.control_elements.pop(elem_index)
        pop_index = elem_index - 1
        if elem_index == 0:
            pop_index = 0
        self.fill_elements.pop(pop_index)
        self.batch_seq_fills.pop(pop_index)
        return elem

    def get_pairs_items(self, elem_index):
        """
        Return's pairs_items list in format:
        pairs_items = [[elem_0, elem_1, fill_index_0],
                       (optional)[elem_0, elem_2, fill_index_1]]
        Used to update fill elements from and to given element
        """
        pairs_items = []
        control_elements_count = len(self.control_elements)
        if control_elements_count < 2:
            return pairs_items

        if elem_index > control_elements_count - 1:
            elem_index = control_elements_count - 1

        elem = self.control_elements[elem_index]

        if elem_index == 0:
            # First control element
            pairs_items = [[elem, self.control_elements[1], 0]]
        elif elem_index == len(self.control_elements) - 1:
            # Last control element
            pairs_items = [[elem, self.control_elements[elem_index - 1], elem_index - 1]]
        elif len(self.control_elements) > 2:
            # At least 3 control elements
            pairs_items = [[elem, self.control_elements[elem_index - 1], elem_index - 1],
                           [elem, self.control_elements[elem_index + 1], elem_index]]

        if self.close and (control_elements_count > 2) and (elem_index in (0, control_elements_count - 1)):
            pairs_items.extend([[self.control_elements[0], self.control_elements[-1], -1]])

        return pairs_items
