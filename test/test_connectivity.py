import os
import numpy as np
import xarray as xr

from unittest import TestCase
from pathlib import Path

import uxarray as ux

current_path = Path(os.path.dirname(os.path.realpath(__file__)))


class TestConnectivity(TestCase):

    # set up class
    @classmethod
    def setUpClass(cls):
        cls.current_path = Path(os.path.dirname(os.path.realpath(__file__)))

    def test_connectivity(self):
        ne30 = self.current_path / 'meshfiles' / 'outCSne30.ug'
        grid_ne30 = ux.open_dataset(ne30, decode_times=False, engine='netcdf4')
        grid_ne30.build_node_face_connectivity()
