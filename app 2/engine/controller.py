"""Required dispatch signals plus a separate, explicitly hypothetical battery model."""
from dataclasses import asdict, dataclass
from datetime import datetime
import math


@dataclass
class BatteryConfig:
    capacity_kwh: float = 1200
    initial_soc_pct: float = 80
    reserve_soc_pct: float = 20
    solar_capacity_kw: float = 150
    max_charge_kw: float = 150
    max_discharge_kw: float = 200
    efficiency: float = 0.95

    def validate(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("Battery settings must be finite numbers.")
        if not 1 <= self.capacity_kwh <= 100000:
            raise ValueError("Battery capacity must be between 1 and 100,000 kWh.")
        if not 0 <= self.reserve_soc_pct <= self.initial_soc_pct <= 100:
            raise ValueError("Battery charge must be 0–100%, with initial charge at or above reserve.")
        if not 0 < self.efficiency <= 1:
            raise ValueError("Battery efficiency must be greater than zero and at most one.")
        if any(not 0 <= v <= 100000 for v in [self.solar_capacity_kw, self.max_charge_kw, self.max_discharge_kw]):
            raise ValueError("Solar and battery power ratings must be 0–100,000 kW.")


def dispatch_signal(demand_kw: float, timestamp: str):
    if not math.isfinite(demand_kw) or demand_kw < 0:
        raise ValueError("Predicted demand must be finite and nonnegative.")
    hour = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").hour
    peak = 18 <= hour <= 22
    high = demand_kw > 160
    return {"grid_mode": "renewable_storage" if high or peak else "main_grid",
            "critical_overload": demand_kw >= 200,
            "reason": "Demand above 160 kW + evening window" if high and peak else "Demand above 160 kW" if high else "Evening window (18:00–22:00)" if peak else "Demand at or below 160 kW, outside evening window"}


def simulate(frame, predictions, config: BatteryConfig):
    config.validate()
    energy = config.capacity_kwh * config.initial_soc_pct / 100
    reserve = config.capacity_kwh * config.reserve_soc_pct / 100
    result = []
    for row, prediction in zip(frame.to_dict(orient="records"), predictions, strict=True):
        demand = float(prediction)
        signal = dispatch_signal(demand, row["timestamp"])
        # One hour per row. PV capacity is a user assumption, not inferred from irradiance alone.
        solar = min(config.solar_capacity_kw, max(0, row["solar_irradiance_wm2"]) / 1000 * config.solar_capacity_kw)
        battery_kw, grid_kw, backup_kw = 0.0, 0.0, 0.0
        if signal["grid_mode"] == "renewable_storage":
            deficit = max(0, demand - solar)
            battery_kw = min(deficit, config.max_discharge_kw, max(0, energy - reserve) * config.efficiency)
            energy -= battery_kw / config.efficiency
            backup_kw = max(0, deficit - battery_kw)
            grid_kw = backup_kw
            surplus = max(0, solar - demand)
        else:
            grid_kw = demand
            surplus = solar
        charge_kw = min(surplus, config.max_charge_kw, max(0, config.capacity_kwh - energy) / config.efficiency)
        energy += charge_kw * config.efficiency
        result.append({"timestamp": row["timestamp"], "target_demand_kw": round(demand, 4), **signal,
                       "battery_soc_pct": round(energy / config.capacity_kwh * 100, 2),
                       "solar_kw": round(solar, 3), "battery_discharge_kw": round(battery_kw, 3),
                       "battery_charge_kw": round(charge_kw, 3), "grid_supply_kw": round(grid_kw, 3),
                       "grid_backup_kw": round(backup_kw, 3), "storage_shortfall": backup_kw > 0.001})
    return result
