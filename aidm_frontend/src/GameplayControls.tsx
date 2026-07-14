import { useState } from 'react'
import { createClientMessageId, type ActionIntent } from './gameActions'
import type {
  GameplayCapabilityControl,
  GameplayControlState,
  GameplaySpellControl,
  GameplayTarget,
} from './gameplayControlState'
import './styles/gameplay-controls.css'

type GameplayControlsProps = {
  state: GameplayControlState
  disabled: boolean
  onSubmit: (message: string, intent: ActionIntent) => boolean
}

function targetLabel(target: GameplayTarget) {
  if (target.currentHp === null || target.maxHp === null) return target.name
  return `${target.name} · ${target.currentHp}/${target.maxHp} HP`
}

function spellTargetCountLabel(spell: GameplaySpellControl) {
  if (spell.minTargets === spell.maxTargets) {
    return `${spell.minTargets} exact ${spell.minTargets === 1 ? 'target' : 'targets'}`
  }
  return `${spell.minTargets}–${spell.maxTargets} exact targets`
}

function capabilityTarget(
  capability: GameplayCapabilityControl,
  selectedTargets: Record<string, string>,
) {
  const selected = capability.targets.find((target) =>
    target.id === selectedTargets[capability.id] && target.available,
  )
  return selected ?? capability.targets.find((target) => target.available) ?? null
}

function selectedHealingAmount(
  capability: GameplayCapabilityControl,
  target: GameplayTarget | null,
  rawAmount: string,
) {
  const parsed = Number.parseInt(rawAmount, 10)
  const missing = target && target.currentHp !== null && target.maxHp !== null
    ? Math.max(0, target.maxHp - target.currentHp)
    : capability.current
  const maximum = Math.max(1, Math.min(capability.current, missing))
  return {
    amount: Math.max(1, Math.min(maximum, Number.isFinite(parsed) ? parsed : 1)),
    maximum,
  }
}

export function GameplayControls({ state, disabled, onSubmit }: GameplayControlsProps) {
  const [selectedSpellId, setSelectedSpellId] = useState('')
  const [selectedSpellTargets, setSelectedSpellTargets] = useState<string[]>([])
  const [selectedCapabilityTargets, setSelectedCapabilityTargets] = useState<Record<string, string>>({})
  const [capabilityAmounts, setCapabilityAmounts] = useState<Record<string, string>>({})
  const selectedSpell = state.spells.find((spell) => spell.id === selectedSpellId) ?? state.spells[0] ?? null
  const eligibleSpellTargetIds = new Set(
    selectedSpell?.targets.filter((target) => target.available).map((target) => target.id) ?? [],
  )
  const spellTargets = selectedSpellTargets
    .filter((targetId) => eligibleSpellTargetIds.has(targetId))
    .slice(0, selectedSpell?.maxTargets ?? 0)
  const spellTargetCountValid = Boolean(
    selectedSpell &&
    spellTargets.length >= selectedSpell.minTargets &&
    spellTargets.length <= selectedSpell.maxTargets,
  )
  const busy = disabled || !state.actorId
  const hasControls = Boolean(
    state.spells.length || state.capabilities.length || state.interactables.length,
  )

  if (!hasControls) return null

  const toggleSpellTarget = (targetId: string, checked: boolean) => {
    if (!selectedSpell) return
    setSelectedSpellTargets((current) => {
      const valid = current.filter((id) => eligibleSpellTargetIds.has(id) && id !== targetId)
      if (!checked) return valid
      if (selectedSpell.maxTargets === 1) return [targetId]
      return [...valid, targetId].slice(0, selectedSpell.maxTargets)
    })
  }

  const castSelectedSpell = () => {
    if (!selectedSpell || !spellTargetCountValid) return
    const names = selectedSpell.targets
      .filter((target) => spellTargets.includes(target.id))
      .map((target) => target.name)
    const targetText = names.length ? ` at ${names.join(', ')}` : ''
    const message = `${state.actorName} casts ${selectedSpell.name}${targetText}.`
    const intent: ActionIntent = {
      kind: 'spell',
      source: 'scene_panel',
      text: message,
      client_message_id: createClientMessageId(),
      spell: {
        name: selectedSpell.name,
        effect: 'Resolve the server-owned spell effect against the selected exact target IDs.',
        resource_pool: 'auto',
        concentration: selectedSpell.concentration,
        target_ids: spellTargets,
      },
    }
    if (onSubmit(message, intent)) setSelectedSpellTargets([])
  }

  const submitCapability = (capability: GameplayCapabilityControl) => {
    const target = capabilityTarget(capability, selectedCapabilityTargets)
    if (!target) return
    const healing = capability.effectType === 'healing_pool'
      ? selectedHealingAmount(capability, target, capabilityAmounts[capability.id] ?? '1')
      : null
    const targetText = capability.targetPolicy === 'self' ? '' : ` on ${target.name}`
    const amountText = healing ? ` for up to ${healing.amount} hit points` : ''
    const message = `${state.actorName} uses ${capability.name}${targetText}${amountText}.`
    const intent: ActionIntent = {
      kind: 'capability',
      source: 'scene_panel',
      text: message,
      client_message_id: createClientMessageId(),
      capability: {
        id: capability.id,
        target_id: target.id,
        ...(healing ? { amount: healing.amount } : {}),
      },
    }
    onSubmit(message, intent)
  }

  return (
    <section className="gameplay-controls" aria-labelledby="gameplay-controls-title">
      <div className="gameplay-controls-heading">
        <div>
          <h3 id="gameplay-controls-title">Mechanical actions</h3>
          <p>Exact targets and resources are checked before the DM narrates the result.</p>
        </div>
        {state.concentration ? (
          <span className="gameplay-control-status">Concentrating: {state.concentration}</span>
        ) : null}
      </div>

      {!state.actorId ? (
        <p className="gameplay-control-warning" role="status">Choose a character to use these actions.</p>
      ) : null}

      {state.spells.length && selectedSpell ? (
        <fieldset className="gameplay-control-group">
          <legend>Authoritative spells</legend>
          <label className="gameplay-control-field">
            <span>Spell</span>
            <select
              aria-label="Authoritative spell"
              value={selectedSpell.id}
              disabled={busy}
              onChange={(event) => {
                setSelectedSpellId(event.target.value)
                setSelectedSpellTargets([])
              }}
            >
              {state.spells.map((spell) => (
                <option key={spell.id} value={spell.id}>
                  {spell.name}{spell.level ? ` · level ${spell.level}` : ' · cantrip'}
                </option>
              ))}
            </select>
          </label>
          <div className="gameplay-control-summary">
            <strong>{selectedSpell.delivery} · {selectedSpell.effectLabel}</strong>
            <span>{selectedSpell.resourceLabel}</span>
            <span>{spellTargetCountLabel(selectedSpell)} · {selectedSpell.rangeBands.join(', ')}</span>
            {selectedSpell.description ? <p>{selectedSpell.description}</p> : null}
          </div>
          <div className="gameplay-target-list" aria-label={`${selectedSpell.name} targets`}>
            {selectedSpell.targets.map((target) => (
              <label
                className={`gameplay-target-option ${target.available ? '' : 'unavailable'}`}
                key={target.id}
                title={target.reason || undefined}
              >
                <input
                  type="checkbox"
                  aria-label={`Target ${target.name}`}
                  checked={spellTargets.includes(target.id)}
                  disabled={busy || !selectedSpell.available || !target.available}
                  onChange={(event) => toggleSpellTarget(target.id, event.target.checked)}
                />
                <span>{targetLabel(target)}</span>
                <small>{target.reason || target.rangeBand}</small>
              </label>
            ))}
          </div>
          {selectedSpell.reason ? (
            <p className="gameplay-control-warning" role="status">{selectedSpell.reason}</p>
          ) : null}
          <button
            type="button"
            className="gameplay-primary-action"
            disabled={busy || !selectedSpell.available || !spellTargetCountValid}
            title={selectedSpell.reason || (!spellTargetCountValid ? spellTargetCountLabel(selectedSpell) : undefined)}
            onClick={castSelectedSpell}
          >
            Cast {selectedSpell.name}
          </button>
        </fieldset>
      ) : null}

      {state.capabilities.length ? (
        <div className="gameplay-control-group" role="group" aria-label="Class capabilities">
          <strong className="gameplay-control-legend">Class capabilities</strong>
          <div className="gameplay-capability-list">
            {state.capabilities.map((capability) => {
              const target = capabilityTarget(capability, selectedCapabilityTargets)
              const healing = capability.effectType === 'healing_pool'
                ? selectedHealingAmount(
                    capability,
                    target,
                    capabilityAmounts[capability.id] ?? '1',
                  )
                : null
              const targetInvalid = !target || !target.available
              return (
                <article className="gameplay-capability" key={capability.id}>
                  <div className="gameplay-capability-heading">
                    <strong>{capability.name}</strong>
                    <span>{capability.current}/{capability.max} · {capability.actionEconomy}</span>
                  </div>
                  {capability.description ? <p>{capability.description}</p> : null}
                  <small>Restores on {capability.refreshesOn || 'long rest'}.</small>
                  {capability.targetPolicy !== 'self' ? (
                    <label className="gameplay-control-field">
                      <span>Exact target</span>
                      <select
                        aria-label={`${capability.name} target`}
                        value={target?.id ?? ''}
                        disabled={busy || !capability.available}
                        onChange={(event) => setSelectedCapabilityTargets((current) => ({
                          ...current,
                          [capability.id]: event.target.value,
                        }))}
                      >
                        {capability.targets.map((candidate) => (
                          <option key={candidate.id} value={candidate.id} disabled={!candidate.available}>
                            {targetLabel(candidate)}{candidate.reason ? ` · ${candidate.reason}` : ''}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <span className="gameplay-self-target">Target: {target?.name ?? state.actorName}</span>
                  )}
                  {healing ? (
                    <label className="gameplay-control-field gameplay-amount-field">
                      <span>Healing points</span>
                      <input
                        type="number"
                        min={1}
                        max={healing.maximum}
                        aria-label={`${capability.name} healing amount`}
                        value={capabilityAmounts[capability.id] ?? '1'}
                        disabled={busy || !capability.available || targetInvalid}
                        onChange={(event) => setCapabilityAmounts((current) => ({
                          ...current,
                          [capability.id]: event.target.value,
                        }))}
                      />
                    </label>
                  ) : null}
                  {capability.reason || target?.reason ? (
                    <p className="gameplay-control-warning">{capability.reason || target?.reason}</p>
                  ) : null}
                  <button
                    type="button"
                    disabled={busy || !capability.available || targetInvalid}
                    title={capability.reason || target?.reason || undefined}
                    onClick={() => submitCapability(capability)}
                  >
                    Use {capability.name}
                  </button>
                </article>
              )
            })}
          </div>
        </div>
      ) : null}

      {state.interactables.length ? (
        <div className="gameplay-control-group" role="group" aria-label="Scene interactions">
          <strong className="gameplay-control-legend">Scene interactions</strong>
          <p className="gameplay-control-help">Current object state and revision are sent with every action.</p>
          <div className="gameplay-object-list">
            {state.interactables.map((object) => (
              <article className="gameplay-object" key={object.id}>
                <div className="gameplay-object-heading">
                  <strong>{object.name}</strong>
                  <span>{object.kind} · {object.revision === null ? 'revision unavailable' : `revision ${object.revision}`}</span>
                </div>
                {object.description ? <p>{object.description}</p> : null}
                {object.states.length ? (
                  <div className="gameplay-object-states" aria-label={`${object.name} state`}>
                    {object.states.map((value) => <span key={value}>{value}</span>)}
                  </div>
                ) : null}
                {object.reason ? <p className="gameplay-control-warning">{object.reason}</p> : null}
                <div className="gameplay-object-actions">
                  {object.actions.map((action) => (
                    <button
                      type="button"
                      key={action.id}
                      disabled={busy || !object.available || !action.available}
                      title={object.reason || action.reason || undefined}
                      onClick={() => {
                        const message = `${state.actorName} attempts to ${action.id} ${object.name}.`
                        onSubmit(message, {
                          kind: 'object',
                          source: 'scene_panel',
                          text: message,
                          client_message_id: createClientMessageId(),
                          object: {
                            id: object.id,
                            action: action.id,
                            ...(object.revision === null ? {} : { revision: object.revision }),
                          },
                        })
                      }}
                    >
                      {action.label}
                    </button>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  )
}
