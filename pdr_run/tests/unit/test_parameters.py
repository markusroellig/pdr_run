import unittest
from pdr_run.models.parameters import (
    compute_mass, compute_radius, from_par_to_string, from_string_to_par
)

class TestParameters(unittest.TestCase):
    def test_compute_mass(self):
        mass = compute_mass(1.0e18, 100.0, 1.5, 0.2)
        self.assertGreater(mass, 0)
    
    def test_compute_radius(self):
        radius = compute_radius(1.0e-3, 100.0, 1.5, 0.2)
        self.assertGreater(radius, 0)
    
    def test_parameter_conversion(self):
        original = 1.0e5
        string_val = from_par_to_string(original)
        converted = from_string_to_par(string_val)
        # Allow for some rounding error
        self.assertAlmostEqual(original, converted, delta=original*0.1)

if __name__ == '__main__':
    unittest.main()