import logging
import warnings

from cellpy.utils.batch_tools.batch_core import BasePlotter


class CyclingSummaryPlotter(BasePlotter):
    def __init__(self):
        super().__init__()

    def do(self):
        logging.debug("Running CyclingSummaryPlotter.do()")
        warnings.warn("Not implemented yet!"
                      "Use your own plotting skills instead")


class EISPlotter(BasePlotter):
    def __init__(self):
        super().__init__()