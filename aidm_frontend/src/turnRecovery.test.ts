import { describe, expect, it } from 'vitest'
import {
  turnRecoveryGateFromSnapshot,
  turnRecoveryGateFromSocketDetails,
} from './turnRecovery'

describe('turn recovery snapshot projection', () => {
  it('distinguishes no applied mechanics from partial pre-DM mechanics', () => {
    expect(turnRecoveryGateFromSnapshot({
      turnRecoveryGate: {
        status: 'required',
        reason: 'post_dm_state_application_failed',
        turnId: 41,
        narrationSaved: true,
        mechanicsApplied: false,
        mechanicsStatus: 'none',
        preDmMechanicsApplied: false,
        preDmAppliedChangeCount: 0,
        postDmMechanicsApplied: false,
        createdAt: '2026-07-12T08:00:00Z',
      },
    })).toMatchObject({
      turnId: 41,
      mechanicsApplied: false,
      mechanicsStatus: 'none',
      preDmMechanicsApplied: false,
      preDmAppliedChangeCount: 0,
      postDmMechanicsApplied: false,
    })

    expect(turnRecoveryGateFromSnapshot({
      turnRecoveryGate: {
        status: 'required',
        reason: 'post_dm_state_application_failed',
        turnId: 42,
        narrationSaved: true,
        mechanicsApplied: true,
        mechanicsStatus: 'partial',
        preDmMechanicsApplied: true,
        preDmAppliedChangeCount: 2,
        postDmMechanicsApplied: false,
        createdAt: '2026-07-12T08:01:00Z',
      },
    })).toMatchObject({
      turnId: 42,
      mechanicsApplied: true,
      mechanicsStatus: 'partial',
      preDmMechanicsApplied: true,
      preDmAppliedChangeCount: 2,
      postDmMechanicsApplied: false,
    })
  })

  it('treats legacy mechanicsApplied evidence conservatively and rejects a completed post-DM phase', () => {
    expect(turnRecoveryGateFromSnapshot({
      turnRecoveryGate: {
        status: 'required',
        reason: 'post_dm_state_application_failed',
        turnId: 43,
        narrationSaved: true,
        mechanicsApplied: true,
        preDmAppliedChangeCount: 0,
      },
    })).toMatchObject({
      mechanicsStatus: 'partial',
      preDmAppliedChangeCount: 1,
    })

    expect(turnRecoveryGateFromSnapshot({
      turnRecoveryGate: {
        status: 'required',
        reason: 'post_dm_state_application_failed',
        turnId: 44,
        narrationSaved: true,
        postDmMechanicsApplied: true,
      },
    })).toBeNull()
  })

  it('projects only safe recovery mechanics from snake-case socket details', () => {
    expect(turnRecoveryGateFromSocketDetails({
      session_id: 20,
      turn_id: 45,
      narration_saved: true,
      mechanics_status: 'partial',
      mechanics_applied: true,
      pre_dm_mechanics_applied: true,
      pre_dm_applied_change_count: 3,
      post_dm_mechanics_applied: false,
      operator_note: 'must never reach the player projection',
    })).toEqual({
      status: 'required',
      reason: 'post_dm_state_application_failed',
      turnId: 45,
      narrationSaved: true,
      mechanicsApplied: true,
      mechanicsStatus: 'partial',
      preDmMechanicsApplied: true,
      preDmAppliedChangeCount: 3,
      postDmMechanicsApplied: false,
      createdAt: '',
    })

    expect(turnRecoveryGateFromSocketDetails({
      turn_id: 45,
      narration_saved: true,
      post_dm_mechanics_applied: true,
    })).toBeNull()
  })
})
