"""uxarray grid module."""
import os
import xarray as xr
import numpy as np

# reader and writer imports
from ._exodus import _read_exodus, _encode_exodus
from ._ugrid import _read_ugrid, _encode_ugrid
from ._shapefile import _read_shpfile
from ._scrip import _read_scrip, _encode_scrip
from .helpers import get_all_face_area_from_coords, parse_grid_type

int_dtype = np.uint32


class Grid:
    """
    Examples
    ----------

    Open an exodus file with Uxarray Grid object

    >>> xarray_obj = xr.open_dataset("filename.g")
    >>> mesh = ux.Grid(xarray_obj)

    Encode as a `xarray.Dataset` in the UGRID format

    >>> mesh.encode_as("ugrid")
    """

    def __init__(self, dataset, **kwargs):
        """Initialize grid variables, decide if loading happens via file, verts
        or gridspec.

        Parameters
        ----------
        dataset : xarray.Dataset, ndarray, list, tuple, required
            Input xarray.Dataset or vertex coordinates that form one face.

        Other Parameters
        ----------------
        islatlon : bool, optional
            Specify if the grid is lat/lon based
        concave: bool, optional
            Specify if this grid has concave elements (internal checks for this are possible)
        gridspec: bool, optional
            Specifies gridspec
        mesh_type: str, optional
            Specify the mesh file type, eg. exo, ugrid, shp, etc

        Raises
        ------
            RuntimeError
                If specified file not found
        """
        # initialize internal variable names
        self.__init_ds_var_names__()

        # initialize face_area variable
        self._face_areas = None

        # initialize indices for antimeridian faces
        #self._antimeridian_faces = None

        # TODO: fix when adding/exercising gridspec

        # unpack kwargs
        # sets default values for all kwargs to None
        kwargs_list = [
            'gridspec', 'vertices', 'islatlon', 'concave', 'source_grid'
        ]
        for key in kwargs_list:
            setattr(self, key, kwargs.get(key, None))

        # check if initializing from verts:
        if isinstance(dataset, (list, tuple, np.ndarray)):
            self.vertices = dataset
            self.__from_vert__()
            self.source_grid = "From vertices"
        # check if initializing from string
        # TODO: re-add gridspec initialization when implemented
        elif isinstance(dataset, xr.Dataset):
            self.mesh_type = parse_grid_type(dataset)
            self.__from_ds__(dataset=dataset)
        else:
            raise RuntimeError("Dataset is not a valid input type.")

        # initialize convenience attributes
        self.__init_grid_var_attrs__()

    def __init_ds_var_names__(self):
        """Populates a dictionary for storing uxarray's internal representation
        of xarray object.

        Note ugrid conventions are flexible with names of variables, see:
        http://ugrid-conventions.github.io/ugrid-conventions/
        """
        self.ds_var_names = {
            "Mesh2": "Mesh2",
            "Mesh2_node_x": "Mesh2_node_x",
            "Mesh2_node_y": "Mesh2_node_y",
            "Mesh2_node_z": "Mesh2_node_z",
            "Mesh2_face_nodes": "Mesh2_face_nodes",
            # initialize dims
            "nMesh2_node": "nMesh2_node",
            "nMesh2_face": "nMesh2_face",
            "nMaxMesh2_face_nodes": "nMaxMesh2_face_nodes"
        }

    def __init_grid_var_attrs__(self) -> None:
        """Initialize attributes for directly accessing UGRID dimensions and
        variables.

        Examples
        ----------
        Assuming the mesh node coordinates for longitude are stored with an input
        name of 'mesh_node_x', we store this variable name in the `ds_var_names`
        dictionary with the key 'Mesh2_node_x'. In order to access it, we do:

        >>> x = grid.ds[grid.ds_var_names["Mesh2_node_x"]]

        With the help of this function, we can directly access it through the
        use of a standardized name based on the UGRID conventions

        >>> x = grid.Mesh2_node_x
        """

        # Iterate over dict to set access attributes
        for key, value in self.ds_var_names.items():
            # Set Attributes for Data Variables
            if self.ds.data_vars is not None:
                if value in self.ds.data_vars:
                    setattr(self, key, self.ds[value])

            # Set Attributes for Coordinates
            if self.ds.coords is not None:
                if value in self.ds.coords:
                    setattr(self, key, self.ds[value])

            # Set Attributes for Dimensions
            if self.ds.dims is not None:
                if value in self.ds.dims:
                    setattr(self, key, len(self.ds[value]))

    def __from_vert__(self):
        """Create a grid with one face with vertices specified by the given
        argument.

        Called by :func:`__init__`.
        """
        self.ds = xr.Dataset()
        self.ds["Mesh2"] = xr.DataArray(
            attrs={
                "cf_role": "mesh_topology",
                "long_name": "Topology data of unstructured mesh",
                "topology_dimension": -1,
                "node_coordinates": "Mesh2_node_x Mesh2_node_y Mesh2_node_z",
                "node_dimension": "nMesh2_node",
                "face_node_connectivity": "Mesh2_face_nodes",
                "face_dimension": "nMesh2_face"
            })
        self.ds.Mesh2.attrs['topology_dimension'] = self.vertices[0].size

        # set default coordinate units to spherical coordinates
        # users can change to cartesian if using cartesian for initialization
        x_units = "degrees_east"
        y_units = "degrees_north"
        if self.vertices[0].size > 2:
            z_units = "elevation"

        x_coord = self.vertices.transpose()[0]
        y_coord = self.vertices.transpose()[1]
        if self.vertices[0].size > 2:
            z_coord = self.vertices.transpose()[2]

        # single face with all nodes
        num_nodes = x_coord.size
        connectivity = [list(range(0, num_nodes))]

        self.ds["Mesh2_node_x"] = xr.DataArray(data=xr.DataArray(x_coord),
                                               dims=["nMesh2_node"],
                                               attrs={"units": x_units})
        self.ds["Mesh2_node_y"] = xr.DataArray(data=xr.DataArray(y_coord),
                                               dims=["nMesh2_node"],
                                               attrs={"units": y_units})
        if self.vertices[0].size > 2:
            self.ds["Mesh2_node_z"] = xr.DataArray(data=xr.DataArray(z_coord),
                                                   dims=["nMesh2_node"],
                                                   attrs={"units": z_units})

        self.ds["Mesh2_face_nodes"] = xr.DataArray(
            data=xr.DataArray(connectivity),
            dims=["nMesh2_face", "nMaxMesh2_face_nodes"],
            attrs={
                "cf_role": "face_node_connectivity",
                "_FillValue": -1,
                "start_index": 0
            })

    # load mesh from a file
    def __from_ds__(self, dataset):
        """Loads a mesh dataset."""
        # call reader as per mesh_type
        if self.mesh_type == "exo":
            self.ds = _read_exodus(dataset, self.ds_var_names)
        elif self.mesh_type == "scrip":
            self.ds = _read_scrip(dataset)
        elif self.mesh_type == "ugrid":
            self.ds, self.ds_var_names = _read_ugrid(dataset, self.ds_var_names)
        elif self.mesh_type == "shp":
            self.ds = _read_shpfile(dataset)
        else:
            raise RuntimeError("unknown mesh type")

        dataset.close()

    def encode_as(self, grid_type):
        """Encodes the grid as a new `xarray.Dataset` per grid format supplied
        in the `grid_type` argument.

        Parameters
        ----------
        grid_type : str, required
            Grid type of output dataset.
            Currently supported options are "ugrid", "exodus", and "scrip"

        Returns
        -------
        out_ds : xarray.Dataset
            The output `xarray.Dataset` that is encoded from the this grid.

        Raises
        ------
        RuntimeError
            If provided grid type or file type is unsupported.
        """

        if grid_type == "ugrid":
            out_ds = _encode_ugrid(self.ds)

        elif grid_type == "exodus":
            out_ds = _encode_exodus(self.ds, self.ds_var_names)

        elif grid_type == "scrip":
            out_ds = _encode_scrip(self.Mesh2_face_nodes, self.Mesh2_node_x,
                                   self.Mesh2_node_y, self.face_areas)
        else:
            raise RuntimeError("The grid type not supported: ", grid_type)

        return out_ds

    def calculate_total_face_area(self, quadrature_rule="triangular", order=4):
        """Function to calculate the total surface area of all the faces in a
        mesh.

        Parameters
        ----------
        quadrature_rule : str, optional
            Quadrature rule to use. Defaults to "triangular".
        order : int, optional
            Order of quadrature rule. Defaults to 4.

        Returns
        -------
        Sum of area of all the faces in the mesh : float
        """

        # call function to get area of all the faces as a np array
        face_areas = self.compute_face_areas(quadrature_rule, order)

        return np.sum(face_areas)

    def compute_face_areas(self, quadrature_rule="triangular", order=4):
        """Face areas calculation function for grid class, calculates area of
        all faces in the grid.

        Parameters
        ----------
        quadrature_rule : str, optional
            Quadrature rule to use. Defaults to "triangular".
        order : int, optional
            Order of quadrature rule. Defaults to 4.

        Returns
        -------
        Area of all the faces in the mesh : np.ndarray

        Examples
        --------
        Open a uxarray grid file

        >>> grid = ux.open_dataset("/home/jain/uxarray/test/meshfiles/outCSne30.ug")

        Get area of all faces in the same order as listed in grid.ds.Mesh2_face_nodes

        >>> grid.get_face_areas
        array([0.00211174, 0.00211221, 0.00210723, ..., 0.00210723, 0.00211221,
            0.00211174])
        """
        if self._face_areas is None:
            # area of a face call needs the units for coordinate conversion if spherical grid is used
            coords_type = "spherical"
            if not "degree" in self.Mesh2_node_x.units:
                coords_type = "cartesian"

            face_nodes = self.Mesh2_face_nodes.data.astype(int_dtype)
            dim = self.Mesh2.attrs['topology_dimension']

            # initialize z
            z = np.zeros((self.nMesh2_node))

            # call func to cal face area of all nodes
            x = self.Mesh2_node_x.data
            y = self.Mesh2_node_y.data
            # check if z dimension
            if self.Mesh2.topology_dimension > 2:
                z = self.Mesh2_node_z.data

            # call function to get area of all the faces as a np array
            self._face_areas = get_all_face_area_from_coords(
                x, y, z, face_nodes, dim, quadrature_rule, order, coords_type)

        return self._face_areas

    # use the property keyword for declaration on face_areas property
    @property
    def face_areas(self):
        """Declare face_areas as a property."""

        if self._face_areas is None:
            self.compute_face_areas()
        return self._face_areas

    def integrate(self, var_ds, quadrature_rule="triangular", order=4):
        """Integrates over all the faces of the given mesh.

        Parameters
        ----------
        var_ds : Xarray dataset, required
            Xarray dataset containing values to integrate on this grid
        quadrature_rule : str, optional
            Quadrature rule to use. Defaults to "triangular".
        order : int, optional
            Order of quadrature rule. Defaults to 4.

        Returns
        -------
        Calculated integral : float

        Examples
        --------
        Open grid file only

        >>> xr_grid = xr.open_dataset("grid.ug")
        >>> grid = ux.Grid.(xr_grid)
        >>> var_ds = xr.open_dataset("centroid_pressure_data_ug")

        # Compute the integral
        >>> integral_psi = grid.integrate(var_ds)
        """
        integral = 0.0

        # call function to get area of all the faces as a np array
        face_areas = self.compute_face_areas(quadrature_rule, order)

        var_key = list(var_ds.keys())
        if len(var_key) > 1:
            # warning: print message
            print(
                "WARNING: The xarray dataset file has more than one variable, using the first variable for integration"
            )
        var_key = var_key[0]
        face_vals = var_ds[var_key].to_numpy()
        integral = np.dot(face_areas, face_vals)

        return integral

    def compute_antimeridian_faces(self, threshold=180.0):
        """Locates any face that crosses the antimeridian'.

        Returns
        -------
        crossed_indices : tuple
            Indices into Mesh2_face_nodes corresponding to any face that
            crossed the antimeridian

        Example
        -------
        Given a face with (x) values
            [a, b, c, d]

        A face can be constructed with sides
            ab, bc, cd, da

        Pad each face
            [a, b, c, d, a]

        Magnitude of each side
            abs([a-b, b-c, c-d, d-a])

        Any value > threshold crosses the antimeridian
        """

        fill_value = self.Mesh2_face_nodes.attrs

        # longitude (x) values of each face
        face_nodes = self.Mesh2_face_nodes.astype(np.int32).values
        face_lon = self.Mesh2_node_x.values[face_nodes]

        # pad last value to properly represent each side
        face_lon_pad = np.pad(face_lon, (0, 1), 'wrap')[:-1]

        # magnitude of each side
        side_diff = np.abs(np.diff(face_lon_pad))

        # true for any node that is part of an edge that crosses antimeridian
        crossed_mask_2d = side_diff > threshold

        # true for any face that contains a node that crosses antimeridian
        crossed_mask_1d = np.any(crossed_mask_2d, axis=1)

        # indices of faces that cross antimeridian
        crossed_indices = tuple(np.argwhere(crossed_mask_1d).squeeze())

        # include only faces that cross antimeridian
        crossed_mask_2d = crossed_mask_2d[crossed_indices, :].squeeze()

        return crossed_indices, crossed_mask_2d

    def split_antimeridian_faces(self):
        """Split any face that crosses the antimeridian into two new faces
        clipped by +/- 180 longitude.

        Returns
        -------
        n_split_faces : int
            to-do
        left_faces : type
            to-do
        right_faces : type

        crossed_indices : tuple
            Indices into Mesh2_face_nodes corresponding to any face that
            crossed the antimeridian
        """

        antimeridian_faces, mask_2d = self.compute_antimeridian_faces()

        # no faces to split
        if len(antimeridian_faces) == 0:
            return None, None, None

        # fill in lon (x) and lat (y) values for the nodes of each face
        antimeridian_indices = self.Mesh2_face_nodes.values[
            antimeridian_faces, :].astype(int)

        face_x = self.Mesh2_node_x.values[antimeridian_indices]
        face_y = self.Mesh2_node_y.values[antimeridian_indices]

        # split faces into left and right faces
        left_x = np.where(face_x > 0, face_x - 360, face_x)
        right_x = np.where(face_x < 0, face_x + 360, face_x)

        # ensure new faces are within [-180, 180]
        left_x = np.clip(left_x, -180, 180)
        right_x = np.clip(right_x, -180, 180)

        # mask for faces that reduce down to a point
        left_exclude = np.all(left_x == -180, axis=1)
        right_exclude = np.all(right_x == 180, axis=1)

        pass
