"""Sector templates. Values are STARTING POINTS for a first estimate and must be replaced with the owner's own figures."""
TEMPLATES = {
    "bakery": {"label": "Bakery", "equipment": [
        {"name": "Oven", "power_kw": 6.0, "hours_per_day": 8, "start_hour": 4, "critical": True, "flexible": False, "value_per_hour": 250, "spoil_per_hour": 0},
        {"name": "Dough mixer", "power_kw": 1.5, "hours_per_day": 3, "start_hour": 3, "critical": False, "flexible": True, "value_per_hour": 80, "spoil_per_hour": 0},
        {"name": "Display fridge", "power_kw": 0.4, "hours_per_day": 24, "start_hour": 0, "critical": True, "flexible": False, "value_per_hour": 20, "spoil_per_hour": 15},
        {"name": "Lighting and tills", "power_kw": 0.5, "hours_per_day": 10, "start_hour": 6, "critical": False, "flexible": False, "value_per_hour": 30, "spoil_per_hour": 0}]},
    "maize_mill": {"label": "Maize or hammer mill", "equipment": [
        {"name": "Hammer mill motor", "power_kw": 11.0, "hours_per_day": 8, "start_hour": 8, "critical": False, "flexible": True, "value_per_hour": 300, "spoil_per_hour": 0},
        {"name": "Sifter and conveyor", "power_kw": 1.5, "hours_per_day": 8, "start_hour": 8, "critical": False, "flexible": True, "value_per_hour": 40, "spoil_per_hour": 0},
        {"name": "Lighting", "power_kw": 0.3, "hours_per_day": 10, "start_hour": 7, "critical": False, "flexible": False, "value_per_hour": 10, "spoil_per_hour": 0}]},
    "cold_room": {"label": "Cold room or butchery", "equipment": [
        {"name": "Cold room compressor", "power_kw": 2.2, "hours_per_day": 24, "start_hour": 0, "critical": True, "flexible": False, "value_per_hour": 50, "spoil_per_hour": 120},
        {"name": "Display freezer", "power_kw": 0.6, "hours_per_day": 24, "start_hour": 0, "critical": True, "flexible": False, "value_per_hour": 30, "spoil_per_hour": 40},
        {"name": "Meat saw", "power_kw": 1.1, "hours_per_day": 4, "start_hour": 8, "critical": False, "flexible": True, "value_per_hour": 100, "spoil_per_hour": 0},
        {"name": "Lighting and tills", "power_kw": 0.4, "hours_per_day": 10, "start_hour": 7, "critical": False, "flexible": False, "value_per_hour": 30, "spoil_per_hour": 0}]},
    "salon": {"label": "Salon or barbershop", "equipment": [
        {"name": "Hair dryers and clippers", "power_kw": 2.0, "hours_per_day": 8, "start_hour": 8, "critical": False, "flexible": False, "value_per_hour": 120, "spoil_per_hour": 0},
        {"name": "Water heater", "power_kw": 3.0, "hours_per_day": 3, "start_hour": 8, "critical": False, "flexible": True, "value_per_hour": 40, "spoil_per_hour": 0},
        {"name": "Lighting and music", "power_kw": 0.3, "hours_per_day": 10, "start_hour": 8, "critical": False, "flexible": False, "value_per_hour": 20, "spoil_per_hour": 0}]},
    "welding": {"label": "Welding or carpentry workshop", "equipment": [
        {"name": "Arc welder", "power_kw": 7.0, "hours_per_day": 5, "start_hour": 8, "critical": False, "flexible": True, "value_per_hour": 200, "spoil_per_hour": 0},
        {"name": "Angle grinders and drills", "power_kw": 2.0, "hours_per_day": 5, "start_hour": 8, "critical": False, "flexible": True, "value_per_hour": 60, "spoil_per_hour": 0},
        {"name": "Lighting", "power_kw": 0.3, "hours_per_day": 9, "start_hour": 8, "critical": False, "flexible": False, "value_per_hour": 10, "spoil_per_hour": 0}]},
}
