from enum import Enum


class RunMethod(str, Enum):
    """How a plan was produced.

    `exact` solves the mixed-integer program to optimality or to a gap. `anneal` searches
    the changeover pattern with simulated annealing and costs each candidate with an exact
    linear program, which is what still runs when a network outgrows a solver licence.
    """

    EXACT = "exact"
    ANNEAL = "anneal"
