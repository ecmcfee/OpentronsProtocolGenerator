from opentrons import protocol_api

metadata = {
    'apiLevel': '2.11',
    'protocolName': 'Automatic Protocol',
    'author': 'Generated'
}

def run(protocol: protocol_api.ProtocolContext):
    # Load labware
    labware_11 = protocol.load_labware('ecmcustom_15_tuberack_14780ul', 11)
    labware_8 = protocol.load_labware('ecmcustom_15_tuberack_14780ul', 8)
    labware_10 = protocol.load_labware('opentrons_96_filtertiprack_200ul', 10)
    labware_1 = protocol.load_module('heaterShakerModuleV1', '1')
    labware_1 = {'labware_1'}.load_labware('ecmcustom_40_wellplate_881.3ul')

    tiprack_200ul = protocol.load_labware('opentrons_96_filtertiprack_200ul', 10)
    pipette = protocol.load_instrument('p300_single', 'right', tip_racks=[tiprack_200ul])
    pipette.max_volume = 200  # Set max volume for p300 pipette adjustment
    pipette.pick_up_tip()
    pipette.aspirate(12, labware_11.wells('A1').bottom(z=6.0))
    pipette.dispense(12, labware_1.wells('A1'))
    pipette.aspirate(100, labware_11.wells('A1').bottom(z=5.0))
    pipette.dispense(100, labware_1.wells('A2'))
    pipette.aspirate(10, labware_11.wells('A1').bottom(z=5.0))
    pipette.dispense(10, labware_1.wells('A3'))
    pipette.pick_up_tip()
    pipette.aspirate(22, labware_11.wells('A2').bottom(z=6.0))
    pipette.dispense(22, labware_1.wells('A1'))
    pipette.pick_up_tip()
    pipette.aspirate(25, labware_11.wells('A4').bottom(z=1))
    pipette.dispense(25, labware_1.wells('B1'))
    pipette.drop_tip()
