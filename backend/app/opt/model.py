"""Multi-period production and distribution planning model.

Formulation
-----------
Indices: line `l`, SKU `k`, plant `p`, distribution centre `d`, period `t`.

Decision variables
    x[l,k,t] >= 0     units produced
    y[l,k,t] in {0,1} line l is set up for SKU k in period t
    s[p,d,k,t] >= 0   units shipped plant -> DC
    I[d,k,t] >= 0     closing inventory at the DC
    u[d,k,t] >= 0     demand left unmet in the period it was due

Objective (minimise)
    unit cost * x  +  changeover cost * y  +  holding * I  +  freight * s  +  penalty * u

Constraints
    (1) capacity      sum_k x[l,k,t] / rate[l,k]  <=  hours[l,t]
    (2) setup link    x[l,k,t]  <=  M[l,k,t] * y[l,k,t]
    (3) minimum run   x[l,k,t]  >=  minrun[l,k] * y[l,k,t]
    (4) line variety  sum_k y[l,k,t]  <=  max_skus_per_line_period
    (5) plant flow    sum_{l in p} x[l,k,t]  =  sum_d s[p,d,k,t]
    (6) DC balance    I[d,k,t]  =  I[d,k,t-1] + sum_p s[p,d,k,t] - demand[d,k,t] + u[d,k,t]
    (7) shelf life    I[d,k,t]  <=  demand at d for k over the next shelf_life periods

The model is always feasible via unmet demand `u`. `M[l,k,t]` is the most the line can
make of SKU k in the period.
"""

from __future__ import annotations

from dataclasses import dataclass

from .solvers import LinExpr, Model, SolveResult
from .types import (
    CapacityShadowPrice,
    CostBreakdown,
    InventoryEntry,
    ProblemData,
    ProductionEntry,
    ShipmentEntry,
    Solution,
    SolveStatus,
    SolverName,
)

# Treat solver noise below this as zero.
TOLERANCE = 1e-6


@dataclass
class _VarIndex:
    production: dict[tuple[str, str, int], int]
    setup: dict[tuple[str, str, int], int]
    shipment: dict[tuple[str, str, str, int], int]
    inventory: dict[tuple[str, str, int], int]
    unmet: dict[tuple[str, str, int], int]
    capacity_rows: dict[tuple[str, int], int]


class PlanningModel:
    """Builds the MILP and translates a raw solver result back into a Solution."""

    def __init__(self, data: ProblemData) -> None:
        self.data = data
        self.model = Model("lensplan")
        self.index = self._build()

    # ------------------------------------------------------------------ building

    def _build(self) -> _VarIndex:
        data = self.data
        model = self.model

        production: dict[tuple[str, str, int], int] = {}
        setup: dict[tuple[str, str, int], int] = {}
        shipment: dict[tuple[str, str, str, int], int] = {}
        inventory: dict[tuple[str, str, int], int] = {}
        unmet: dict[tuple[str, str, int], int] = {}
        capacity_rows: dict[tuple[str, int], int] = {}

        sku_by_id = {s.id: s for s in data.skus}

        for cap in data.capabilities:
            for t in data.periods:
                hours = data.hours_for(cap.line_id, t)
                max_units = min(
                    hours * cap.units_per_hour,
                    self._useful_production_bound(cap.sku_id, t),
                )
                x = model.add_var(
                    f"x[{cap.line_id},{cap.sku_id},{t}]", lower=0.0, upper=max_units
                )
                y = model.add_var(f"y[{cap.line_id},{cap.sku_id},{t}]", binary=True)
                production[(cap.line_id, cap.sku_id, t)] = x.index
                setup[(cap.line_id, cap.sku_id, t)] = y.index
                model.set_objective_term(x, cap.unit_cost)
                model.set_objective_term(y, cap.changeover_cost)

        for lane in data.lanes:
            for sku in data.skus:
                for t in data.periods:
                    v = model.add_var(f"s[{lane.plant_id},{lane.dc_id},{sku.id},{t}]")
                    shipment[(lane.plant_id, lane.dc_id, sku.id, t)] = v.index
                    model.set_objective_term(v, lane.cost_per_unit)

        for dc in data.dcs:
            for sku in data.skus:
                for t in data.periods:
                    inv = model.add_var(f"I[{dc.id},{sku.id},{t}]")
                    inventory[(dc.id, sku.id, t)] = inv.index
                    model.set_objective_term(inv, sku.holding_cost)

                    # Unmet demand cannot exceed demand.
                    demanded = data.demand_for(dc.id, sku.id, t)
                    short = model.add_var(
                        f"u[{dc.id},{sku.id},{t}]", lower=0.0, upper=demanded
                    )
                    unmet[(dc.id, sku.id, t)] = short.index
                    model.set_objective_term(short, sku.stockout_penalty)

        idx = _VarIndex(production, setup, shipment, inventory, unmet, capacity_rows)

        self._add_capacity(idx)
        self._add_setup_linking(idx)
        self._add_line_variety(idx)
        self._add_plant_flow(idx)
        self._add_dc_balance(idx, sku_by_id)
        self._add_shelf_life(idx, sku_by_id)
        return idx

    def _var(self, index: int, binary: bool = False):
        from .solvers import Var

        return Var(index=index, name="", is_binary=binary)

    def _add_capacity(self, idx: _VarIndex) -> None:
        data = self.data
        for line in data.lines:
            for t in data.periods:
                hours = data.hours_for(line.id, t)
                expr = LinExpr()
                used = False
                for sku in data.skus:
                    cap = data.capability(line.id, sku.id)
                    if cap is None:
                        continue
                    expr.add(
                        self._var(idx.production[(line.id, sku.id, t)]),
                        1.0 / cap.units_per_hour,
                    )
                    used = True
                if not used:
                    continue
                idx.capacity_rows[(line.id, t)] = self.model.num_constraints
                self.model.add_constraint(expr, upper=hours, name=f"cap[{line.id},{t}]")

    def _useful_production_bound(self, sku_id: str, period: int) -> float:
        """Most of a SKU that could ever be worth making in one period.

        Anything produced in period t can only serve demand from t onward, so remaining
        demand bounds it. For slow movers that is far tighter than line capacity, and a
        tight big-M is the difference between a relaxation the solver can prune against
        and one it cannot.

        Opening inventory is only netted off in the first period. It is consumed as the
        horizon advances, so subtracting it again at later periods understates what still
        has to be built and cuts valid plans out of the feasible region.
        """
        data = self.data
        position = data.periods.index(period)
        remaining = sum(
            data.demand_for(dc.id, sku_id, future)
            for dc in data.dcs
            for future in data.periods[position:]
        )
        if period == data.periods[0]:
            remaining -= sum(
                data.opening_inventory.get((dc.id, sku_id), 0.0) for dc in data.dcs
            )
        return max(0.0, remaining)

    def _add_setup_linking(self, idx: _VarIndex) -> None:
        data = self.data
        for cap in data.capabilities:
            for t in data.periods:
                hours = data.hours_for(cap.line_id, t)
                big_m = min(
                    hours * cap.units_per_hour,
                    self._useful_production_bound(cap.sku_id, t),
                )
                x = self._var(idx.production[(cap.line_id, cap.sku_id, t)])
                y = self._var(idx.setup[(cap.line_id, cap.sku_id, t)], binary=True)

                link = LinExpr().add(x, 1.0).add(y, -big_m)
                self.model.add_constraint(
                    link, upper=0.0, name=f"setup[{cap.line_id},{cap.sku_id},{t}]"
                )

                if cap.min_run_units > 0:
                    minrun = LinExpr().add(x, 1.0).add(y, -cap.min_run_units)
                    self.model.add_constraint(
                        minrun, lower=0.0, name=f"minrun[{cap.line_id},{cap.sku_id},{t}]"
                    )

    def _add_line_variety(self, idx: _VarIndex) -> None:
        limit = self.data.max_skus_per_line_period
        if not limit:
            return
        for line in self.data.lines:
            for t in self.data.periods:
                expr = LinExpr()
                present = False
                for sku in self.data.skus:
                    key = (line.id, sku.id, t)
                    if key in idx.setup:
                        expr.add(self._var(idx.setup[key], binary=True), 1.0)
                        present = True
                if present:
                    self.model.add_constraint(
                        expr, upper=float(limit), name=f"variety[{line.id},{t}]"
                    )

    def _add_plant_flow(self, idx: _VarIndex) -> None:
        data = self.data
        for plant in data.plants:
            lines = data.lines_at(plant.id)
            for sku in data.skus:
                for t in data.periods:
                    expr = LinExpr()
                    present = False
                    for line in lines:
                        key = (line.id, sku.id, t)
                        if key in idx.production:
                            expr.add(self._var(idx.production[key]), 1.0)
                            present = True
                    for dc in data.dcs:
                        key = (plant.id, dc.id, sku.id, t)
                        if key in idx.shipment:
                            expr.add(self._var(idx.shipment[key]), -1.0)
                            present = True
                    if present:
                        self.model.add_constraint(
                            expr, lower=0.0, upper=0.0, name=f"flow[{plant.id},{sku.id},{t}]"
                        )

    def _add_dc_balance(self, idx: _VarIndex, sku_by_id: dict) -> None:
        data = self.data
        first = data.periods[0]
        for dc in data.dcs:
            for sku in data.skus:
                previous: int | None = None
                for t in data.periods:
                    demanded = data.demand_for(dc.id, sku.id, t)
                    expr = LinExpr()
                    expr.add(self._var(idx.inventory[(dc.id, sku.id, t)]), 1.0)
                    if previous is not None:
                        expr.add(self._var(previous), -1.0)
                    for plant in data.plants:
                        key = (plant.id, dc.id, sku.id, t)
                        if key in idx.shipment:
                            expr.add(self._var(idx.shipment[key]), -1.0)
                    expr.add(self._var(idx.unmet[(dc.id, sku.id, t)]), -1.0)

                    opening = (
                        data.opening_inventory.get((dc.id, sku.id), 0.0)
                        if t == first
                        else 0.0
                    )
                    rhs = opening - demanded
                    self.model.add_constraint(
                        expr, lower=rhs, upper=rhs, name=f"bal[{dc.id},{sku.id},{t}]"
                    )
                    previous = idx.inventory[(dc.id, sku.id, t)]

    def _add_shelf_life(self, idx: _VarIndex, sku_by_id: dict) -> None:
        """Cap stock at what can still be consumed before it expires.

        Without this the optimiser will happily build ahead into a cheap period and hold
        the result indefinitely, which is not a plan a planner can execute for a product
        with a dated shelf life.
        """
        data = self.data
        for dc in data.dcs:
            for sku in data.skus:
                life = sku.shelf_life_periods
                if life <= 0:
                    continue
                for position, t in enumerate(data.periods):
                    window = data.periods[position + 1 : position + 1 + life]
                    consumable = sum(
                        data.demand_for(dc.id, sku.id, future) for future in window
                    )
                    horizon_remaining = len(data.periods) - position - 1
                    if life >= horizon_remaining:
                        # Shelf life outlasts the planning horizon, so it cannot bind.
                        continue
                    expr = LinExpr().add(self._var(idx.inventory[(dc.id, sku.id, t)]), 1.0)
                    self.model.add_constraint(
                        expr, upper=consumable, name=f"shelf[{dc.id},{sku.id},{t}]"
                    )

    # ------------------------------------------------------------------ solving

    def solve(
        self,
        solver: SolverName,
        *,
        time_limit: float = 60.0,
        mip_gap: float = 1e-4,
        with_sensitivity: bool = True,
    ) -> Solution:
        result = self.model.solve(solver, time_limit=time_limit, mip_gap=mip_gap)
        solution = self.to_solution(result, solver)
        if with_sensitivity and solution.status in (
            SolveStatus.OPTIMAL,
            SolveStatus.FEASIBLE,
            SolveStatus.TIME_LIMIT,
        ):
            solution.shadow_prices = self.capacity_shadow_prices(solver, solution)
        return solution

    def capacity_shadow_prices(
        self, solver: SolverName, solution: Solution
    ) -> list[CapacityShadowPrice]:
        """Marginal value of an extra hour on each line, from the LP relaxation.

        Duals do not exist for a mixed-integer program, so this relaxes the setup
        binaries. The number answers "where is another hour worth buying" well enough to
        direct attention, and is reported as guidance rather than an exact marginal cost.
        """
        relaxed = self.model.solve(
            solver, time_limit=30.0, mip_gap=0.0, relax_binaries=True
        )
        if not relaxed.duals:
            return []

        hours_used: dict[tuple[str, int], float] = {}
        for entry in solution.production:
            key = (entry.line_id, entry.period)
            hours_used[key] = hours_used.get(key, 0.0) + entry.hours

        # Store both MIP and relaxation utilisation next to the dual.
        relaxed_hours: dict[tuple[str, int], float] = {}
        for (line_id, sku_id, period), column in self.index.production.items():
            capability = self.data.capability(line_id, sku_id)
            if capability is None or column >= len(relaxed.values):
                continue
            key = (line_id, period)
            relaxed_hours[key] = (
                relaxed_hours.get(key, 0.0)
                + relaxed.values[column] / capability.units_per_hour
            )

        prices: list[CapacityShadowPrice] = []
        for (line_id, period), row in self.index.capacity_rows.items():
            if row >= len(relaxed.duals):
                continue
            dual = relaxed.duals[row]
            available = self.data.hours_for(line_id, period)
            prices.append(
                CapacityShadowPrice(
                    line_id=line_id,
                    period=period,
                    # Binding upper-bound duals are negative under minimisation.
                    shadow_price=abs(dual),
                    hours_available=available,
                    hours_used=hours_used.get((line_id, period), 0.0),
                    hours_used_relaxed=relaxed_hours.get((line_id, period), 0.0),
                )
            )
        prices.sort(key=lambda p: p.shadow_price, reverse=True)
        return prices

    def to_solution(self, result: SolveResult, solver: SolverName) -> Solution:
        data = self.data
        total_demand = sum(data.demand.values())
        empty_costs = CostBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)

        if not result.has_solution:
            return Solution(
                status=result.status,
                solver=solver,
                objective=0.0,
                costs=empty_costs,
                production=[],
                shipments=[],
                inventory=[],
                solve_seconds=result.seconds,
                total_demand_units=total_demand,
                mip_gap=result.mip_gap,
            )

        values = result.values
        sku_by_id = {s.id: s for s in data.skus}

        production: list[ProductionEntry] = []
        production_cost = 0.0
        changeover_cost = 0.0
        for cap in data.capabilities:
            for t in data.periods:
                units = values[self.index.production[(cap.line_id, cap.sku_id, t)]]
                is_setup = values[self.index.setup[(cap.line_id, cap.sku_id, t)]] > 0.5
                if units <= TOLERANCE and not is_setup:
                    continue
                units = max(0.0, units)
                production_cost += units * cap.unit_cost
                if is_setup:
                    changeover_cost += cap.changeover_cost
                production.append(
                    ProductionEntry(
                        line_id=cap.line_id,
                        sku_id=cap.sku_id,
                        period=t,
                        units=units,
                        hours=units / cap.units_per_hour,
                        changeover=is_setup,
                    )
                )

        shipments: list[ShipmentEntry] = []
        freight_cost = 0.0
        for lane in data.lanes:
            for sku in data.skus:
                for t in data.periods:
                    units = values[
                        self.index.shipment[(lane.plant_id, lane.dc_id, sku.id, t)]
                    ]
                    if units <= TOLERANCE:
                        continue
                    freight_cost += units * lane.cost_per_unit
                    shipments.append(
                        ShipmentEntry(
                            plant_id=lane.plant_id,
                            dc_id=lane.dc_id,
                            sku_id=sku.id,
                            period=t,
                            units=units,
                        )
                    )

        inventory: list[InventoryEntry] = []
        holding_cost = 0.0
        stockout_cost = 0.0
        for dc in data.dcs:
            for sku in data.skus:
                for t in data.periods:
                    closing = values[self.index.inventory[(dc.id, sku.id, t)]]
                    short = values[self.index.unmet[(dc.id, sku.id, t)]]
                    closing = max(0.0, closing)
                    short = max(0.0, short)
                    holding_cost += closing * sku.holding_cost
                    stockout_cost += short * sku.stockout_penalty
                    if closing <= TOLERANCE and short <= TOLERANCE:
                        continue
                    inventory.append(
                        InventoryEntry(
                            dc_id=dc.id,
                            sku_id=sku.id,
                            period=t,
                            closing_units=closing,
                            unmet_units=short,
                        )
                    )

        costs = CostBreakdown(
            production=production_cost,
            changeover=changeover_cost,
            holding=holding_cost,
            freight=freight_cost,
            stockout=stockout_cost,
        )
        return Solution(
            status=result.status,
            solver=solver,
            objective=result.objective,
            costs=costs,
            production=production,
            shipments=shipments,
            inventory=inventory,
            solve_seconds=result.seconds,
            total_demand_units=total_demand,
            mip_gap=result.mip_gap,
        )


def solve_plan(
    data: ProblemData,
    solver: SolverName = SolverName.HIGHS,
    *,
    time_limit: float = 60.0,
    mip_gap: float = 1e-4,
    with_sensitivity: bool = True,
) -> Solution:
    return PlanningModel(data).solve(
        solver, time_limit=time_limit, mip_gap=mip_gap, with_sensitivity=with_sensitivity
    )
