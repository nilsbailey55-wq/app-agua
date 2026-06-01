"""Tests para las funciones puras de generate_land_report.py.

Solo testea funciones sin I/O: linreg, scoring, interpolación, colores.
No toca APIs externas ni archivos de datos.
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from generate_land_report import (
    linreg,
    interp_value,
    drought_risk_score,
    excess_risk_score,
    color_for_score,
    color_class,
)


class TestLinreg(unittest.TestCase):

    def test_perfect_positive_trend(self):
        years = [2010, 2011, 2012, 2013, 2014]
        values = [100.0, 110.0, 120.0, 130.0, 140.0]
        slope, intercept, r = linreg(years, values)
        self.assertAlmostEqual(slope, 10.0, places=5)
        self.assertAlmostEqual(r, 1.0, places=5)

    def test_perfect_negative_trend(self):
        years = [2010, 2011, 2012]
        values = [300.0, 200.0, 100.0]
        slope, _, r = linreg(years, values)
        self.assertAlmostEqual(slope, -100.0, places=5)
        self.assertAlmostEqual(r, -1.0, places=5)

    def test_flat_series_zero_slope(self):
        years = [2000, 2001, 2002, 2003]
        values = [500.0, 500.0, 500.0, 500.0]
        slope, _, r = linreg(years, values)
        self.assertEqual(slope, 0)
        self.assertEqual(r, 0)

    def test_two_points(self):
        slope, _, _ = linreg([2020, 2021], [100.0, 120.0])
        self.assertAlmostEqual(slope, 20.0, places=5)

    def test_returns_three_values(self):
        result = linreg([2000, 2001], [1.0, 2.0])
        self.assertEqual(len(result), 3)


class TestDroughtRiskScore(unittest.TestCase):

    def test_zero_risk_good_conditions(self):
        # Sin tendencia negativa, baja variabilidad, anomalía positiva
        score = drought_risk_score(slope_per_year=5.0, cv=0.0,
                                   recent_anom_pct=10.0, mean_yearly=800)
        self.assertEqual(score, 0)

    def test_high_risk_bad_conditions(self):
        # Tendencia muy negativa, alta variabilidad, anomalía muy negativa
        score = drought_risk_score(slope_per_year=-10.0, cv=0.5,
                                   recent_anom_pct=-60.0, mean_yearly=400)
        self.assertGreaterEqual(score, 70)

    def test_score_within_bounds(self):
        # Condiciones extremas no deben salirse de 0-100
        for slope in [-100, -5, 0, 5]:
            for cv in [0, 0.3, 1.0, 2.0]:
                for anom in [-100, -30, 0, 30]:
                    s = drought_risk_score(slope, cv, anom, 700)
                    self.assertGreaterEqual(s, 0,
                        f"score negativo con slope={slope} cv={cv} anom={anom}")
                    self.assertLessEqual(s, 100,
                        f"score>100 con slope={slope} cv={cv} anom={anom}")

    def test_trend_component_only_negative_slope(self):
        # Slope positivo no suma puntos de tendencia
        s_pos = drought_risk_score(slope_per_year=10.0, cv=0, recent_anom_pct=0, mean_yearly=800)
        s_zero = drought_risk_score(slope_per_year=0.0, cv=0, recent_anom_pct=0, mean_yearly=800)
        self.assertEqual(s_pos, s_zero)

    def test_recent_anom_only_negative_contributes(self):
        # Anomalía positiva no debe aumentar el score
        s_pos = drought_risk_score(0, 0.1, recent_anom_pct=50, mean_yearly=800)
        s_zero = drought_risk_score(0, 0.1, recent_anom_pct=0, mean_yearly=800)
        self.assertEqual(s_pos, s_zero)


class TestExcessRiskScore(unittest.TestCase):

    def test_zero_risk(self):
        self.assertEqual(excess_risk_score(0, 0), 0)

    def test_high_risk(self):
        score = excess_risk_score(historical_extremes=5, body_growth_count=3)
        self.assertGreaterEqual(score, 60)

    def test_score_within_bounds(self):
        for extremes in range(0, 10):
            for growth in range(0, 5):
                s = excess_risk_score(extremes, growth)
                self.assertGreaterEqual(s, 0)
                self.assertLessEqual(s, 100)

    def test_extremes_capped_at_60(self):
        # Con 0 cuerpos de agua crecientes, máximo posible es 60
        s = excess_risk_score(historical_extremes=100, body_growth_count=0)
        self.assertEqual(s, 60)

    def test_body_growth_capped_at_40(self):
        # Con 0 extremos, máximo posible es 40
        s = excess_risk_score(historical_extremes=0, body_growth_count=100)
        self.assertEqual(s, 40)


class TestColorForScore(unittest.TestCase):

    def test_low_risk_green(self):
        self.assertEqual(color_for_score(0),  '#2e7d32')
        self.assertEqual(color_for_score(29), '#2e7d32')

    def test_medium_yellow(self):
        self.assertEqual(color_for_score(30), '#f9a825')
        self.assertEqual(color_for_score(49), '#f9a825')

    def test_medium_high_orange(self):
        self.assertEqual(color_for_score(50), '#ef6c00')
        self.assertEqual(color_for_score(69), '#ef6c00')

    def test_high_red(self):
        self.assertEqual(color_for_score(70), '#c62828')
        self.assertEqual(color_for_score(100), '#c62828')


class TestColorClass(unittest.TestCase):

    def test_labels(self):
        self.assertEqual(color_class(0),   'Bajo')
        self.assertEqual(color_class(29),  'Bajo')
        self.assertEqual(color_class(30),  'Medio')
        self.assertEqual(color_class(49),  'Medio')
        self.assertEqual(color_class(50),  'Medio-Alto')
        self.assertEqual(color_class(69),  'Medio-Alto')
        self.assertEqual(color_class(70),  'Alto')
        self.assertEqual(color_class(100), 'Alto')


class TestInterpValue(unittest.TestCase):

    def _make_corners(self, lat0, lng0, vals):
        """Crea el dict de corners con 4 puntos alrededor de (lat0, lng0)."""
        return {
            (lat0,   lng0  ): {'v': vals[0]},
            (lat0,   lng0+2): {'v': vals[1]},
            (lat0+2, lng0  ): {'v': vals[2]},
            (lat0+2, lng0+2): {'v': vals[3]},
        }

    def test_center_point_average(self):
        # En el centro exacto del cuadrado, todos los corners tienen el mismo peso
        corners = self._make_corners(-34, -64, [100.0, 100.0, 100.0, 100.0])
        result = interp_value(-33, -63, corners, lambda p: p['v'])
        self.assertAlmostEqual(result, 100.0, places=3)

    def test_closer_corner_dominates(self):
        # Punto muy cerca de un corner → ese valor domina
        corners = self._make_corners(-34, -64, [200.0, 10.0, 10.0, 10.0])
        result = interp_value(-33.9, -63.9, corners, lambda p: p['v'])
        self.assertGreater(result, 150.0)   # domina el 200

    def test_missing_corners_returns_none(self):
        corners = {(-34, -64): None, (-34, -62): None,
                   (-32, -64): None, (-32, -62): None}
        result = interp_value(-33, -63, corners, lambda p: p['v'])
        self.assertIsNone(result)

    def test_partial_corners(self):
        # Con sólo algunos corners válidos, no debe crashear
        corners = {
            (-34, -64): {'v': 500.0},
            (-34, -62): None,
            (-32, -64): None,
            (-32, -62): None,
        }
        result = interp_value(-33.9, -63.9, corners, lambda p: p['v'])
        self.assertIsNotNone(result)
        self.assertEqual(result, 500.0)


if __name__ == "__main__":
    unittest.main()
