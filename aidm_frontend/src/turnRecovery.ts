import type { JsonRecord } from './types'

export type TurnRecoveryResolution =
  | 'state_corrected'
  | 'no_mechanical_change_required'

export type TurnRecoveryGate = {
  status: 'required'
  reason: 'post_dm_state_application_failed'
  turnId: number
  narrationSaved: true
  mechanicsApplied: boolean
  mechanicsStatus: 'none' | 'partial'
  preDmMechanicsApplied: boolean
  preDmAppliedChangeCount: number
  postDmMechanicsApplied: false
  createdAt: string
}

export type TurnRecoveryResponse = {
  resolved: true
  idempotent_replay: boolean
  session_id: number
  turn_id: number
  resolution: TurnRecoveryResolution
  state_revision: number
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function positiveInteger(value: unknown) {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function nonNegativeInteger(value: unknown) {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null
}

export function turnRecoveryGateFromSnapshot(snapshot: unknown): TurnRecoveryGate | null {
  if (!isRecord(snapshot) || !isRecord(snapshot.turnRecoveryGate)) return null
  const gate = snapshot.turnRecoveryGate
  const turnId = positiveInteger(gate.turnId)
  if (
    gate.status !== 'required' ||
    gate.reason !== 'post_dm_state_application_failed' ||
    turnId === null ||
    gate.narrationSaved !== true ||
    (gate.postDmMechanicsApplied !== undefined && gate.postDmMechanicsApplied !== false)
  ) {
    return null
  }
  const rawPreDmAppliedChangeCount = nonNegativeInteger(gate.preDmAppliedChangeCount)
  const partialMechanics =
    gate.mechanicsStatus === 'partial' ||
    gate.mechanicsApplied === true ||
    gate.preDmMechanicsApplied === true ||
    (rawPreDmAppliedChangeCount !== null && rawPreDmAppliedChangeCount > 0)
  const preDmAppliedChangeCount = partialMechanics
    ? Math.max(1, rawPreDmAppliedChangeCount ?? 0)
    : 0
  return {
    status: 'required',
    reason: 'post_dm_state_application_failed',
    turnId,
    narrationSaved: true,
    mechanicsApplied: partialMechanics,
    mechanicsStatus: partialMechanics ? 'partial' : 'none',
    preDmMechanicsApplied: partialMechanics,
    preDmAppliedChangeCount,
    postDmMechanicsApplied: false,
    createdAt: typeof gate.createdAt === 'string' ? gate.createdAt : '',
  }
}

export function turnRecoveryGateFromSocketDetails(details: unknown): TurnRecoveryGate | null {
  if (!isRecord(details)) return null
  return turnRecoveryGateFromSnapshot({
    turnRecoveryGate: {
      status: 'required',
      reason: 'post_dm_state_application_failed',
      turnId: details.turn_id,
      narrationSaved: details.narration_saved,
      mechanicsStatus: details.mechanics_status,
      mechanicsApplied: details.mechanics_applied,
      preDmMechanicsApplied: details.pre_dm_mechanics_applied,
      preDmAppliedChangeCount: details.pre_dm_applied_change_count,
      postDmMechanicsApplied: details.post_dm_mechanics_applied,
      createdAt: '',
    },
  })
}
