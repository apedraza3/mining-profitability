from fleet.adapters.clore import CloreAdapter
from fleet.adapters.electricity import ElectricityAdapter
from fleet.adapters.foreman import ForemanAdapter
from fleet.adapters.kryptex import KryptexAdapter
from fleet.adapters.lan_asic import LanAsicAdapter
from fleet.adapters.powerpool import PowerPoolAdapter


def all_adapters():
    return [
        PowerPoolAdapter(),
        ForemanAdapter(),
        CloreAdapter(),
        KryptexAdapter(),
        LanAsicAdapter(),
        ElectricityAdapter(),
    ]
