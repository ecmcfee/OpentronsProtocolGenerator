from opentrons import protocol_api

metadata = {
    'apiLevel': '2.15',
    'protocolName': 'Automatic Protocol',
    'author': 'Generated'
}

def run(protocol: protocol_api.ProtocolContext):
    # Load labware
    labware_7 = protocol.load_labware('20mlscintvials_12_wellplate_20000ul', 7)
    tiprack_200ul = protocol.load_labware('opentrons_96_filtertiprack_200ul', 10)
    tiprack_1000ul = protocol.load_labware('opentrons_flex_96_filtertiprack_1000ul', 11)

    p300 = protocol.load_instrument('p300_single_gen2', 'left', tip_racks=[tiprack_200ul])
    p1000 = protocol.load_instrument('p1000_single_gen2', 'right', tip_racks=[tiprack_1000ul])

    p1000.pick_up_tip()
    p1000.aspirate(900, labware_7['A1'].bottom(z=1.0))
    p1000.dispense(900, labware_7['A2'].top(z=-3))
    p1000.touch_tip(labware_7['A2'], radius=0.8, v_offset=-1, speed=60)
    p1000.aspirate(600.0, labware_7['A1'].bottom(z=1.0))
    p1000.dispense(600.0, labware_7['A2'].top(z=-3))
    p1000.mix(5, 720.0, labware_7['A2'].bottom(z=2.0))
    p1000.touch_tip(labware_7['A2'], radius=0.8, v_offset=-1, speed=60)
    p1000.drop_tip()
