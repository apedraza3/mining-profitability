from fleet.adapters.clore import CloreAdapter
from fleet.adapters.electricity import ElectricityAdapter
from fleet.adapters.foreman import ForemanAdapter
from fleet.adapters.homeassistant import HomeAssistantAdapter
from fleet.adapters.kryptex import KryptexAdapter
from fleet.adapters.lan_asic import LanAsicAdapter
from fleet.adapters.pdu import PduAdapter
from fleet.adapters.powerpool import PowerPoolAdapter
from fleet.adapters.vast import VastAdapter


def all_adapters():
    return [
        PowerPoolAdapter(),
        ForemanAdapter(),
        CloreAdapter(),
        VastAdapter(),
        KryptexAdapter(),
        LanAsicAdapter(),
        ElectricityAdapter(),
        PduAdapter(),
        HomeAssistantAdapter(),
    ]
