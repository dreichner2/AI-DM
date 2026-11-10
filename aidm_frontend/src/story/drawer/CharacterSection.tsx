import { Fragment, useMemo, useState } from 'react'
import { characterVitality, partyVitalityFromSnapshot, type SavingThrowSummary } from '../../gameSelectors'
import type { InspectorPanelProps } from '../../InspectorPanel'
import type { SessionBoardProps } from '../../SessionBoard'
import { profileIconSrcForCharacter } from '../../profileIcons'
import type { ActivePlayer } from '../../types'
import { StoryVitality } from '../StoryVitality'

/** Conditions the vitality mark already shows. */
const VITALITY_CONDITIONS = new Set(['dying', 'stable', 'dead', 'unconscious'])

const ABILITY_NAMES: Record<string, string> = {
  STR: 'Strength',
  DEX: 'Dexterity',
  CON: 'Constitution',
  INT: 'Intelligence',
  WIS: 'Wisdom',
  CHA: 'Charisma',
}

function mechanicLabel(value: string) {
  return value
    .replace(/^tool:/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

/** A bonus with its sign: "2" reads "+2", "-1" stays "-1"; anything that is not a number is shown as it is. */
function signed(value: string) {
  const trimmed = value.trim().replace(/^\+/, '')
  const number = Number(trimmed)
  if (!trimmed || !Number.isFinite(number)) return value
  return number >= 0 ? `+${number}` : String(number)
}

/**
 * HP as current/max: the live roster first, then the sheet. The sheet drops its maximum when the
 * character is at full health, so only the roster can supply it then.
 */
function hpLabel(hp: string, player: ActivePlayer | undefined) {
  if (player?.health && player.health.maxHp > 0) return `${player.health.currentHp}/${player.health.maxHp}`
  return hp.replace(/\s*\/\s*/, '/')
}

/** Health tone for the HP numeral: the live roster first, then the sheet's own numbers. */
function hpTone(hp: string, player: ActivePlayer | undefined) {
  if (player?.health) return player.health.tone
  const [current, max] = hp.split('/').map((part) => Number.parseFloat(part))
  if (!Number.isFinite(current) || !Number.isFinite(max) || max <= 0) return undefined
  if (current <= 0) return 'down'
  const ratio = current / max
  return ratio <= 0.25 ? 'critical' : ratio <= 0.6 ? 'hurt' : 'healthy'
}

/** Proficient saves first, each group in the usual STR…CHA order. */
function orderedSaves(saves: SavingThrowSummary[]) {
  return [...saves.filter((save) => save.proficient), ...saves.filter((save) => !save.proficient)]
}

function uniqueConditions(...lists: string[][]) {
  const seen = new Set<string>()
  return lists.flat().filter((condition) => {
    const key = condition.trim().toLowerCase()
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function playerAvatar(player: ActivePlayer) {
  return (
    player.profile_image ||
    profileIconSrcForCharacter({ race: player.race, sex: player.sex }) ||
    '/profile-icons/human_male.png'
  )
}

type CharacterSectionProps = {
  board: SessionBoardProps
  inspector: InspectorPanelProps
}

export function CharacterSection({ board, inspector }: CharacterSectionProps) {
  const [manageOpen, setManageOpen] = useState(false)
  const { displayCharacter, statBlock, xpProgress } = inspector
  const viewer = inspector.activePlayers.find((player) => player.id === inspector.selectedPlayerId)
  const snapshot = board.sessionState?.state_snapshot
  const partyVitality = useMemo(() => partyVitalityFromSnapshot(snapshot), [snapshot])
  const combatConditions =
    board.combatState.allies.find((ally) => ally.name === displayCharacter.name)?.conditions ?? []
  const allConditions = uniqueConditions(statBlock.conditions ?? [], combatConditions)
  const vitality =
    (inspector.selectedPlayerId !== null ? partyVitality[inspector.selectedPlayerId] : undefined) ??
    characterVitality(allConditions, statBlock.deathSaves)
  const conditions = vitality
    ? allConditions.filter((condition) => !VITALITY_CONDITIONS.has(condition.trim().toLowerCase()))
    : allConditions
  const saves = orderedSaves(statBlock.savingThrows ?? [])
  const hitDice = statBlock.hitDice ?? null
  const skills = (statBlock.skillProficiencies ?? []).map((skill) =>
    `${mechanicLabel(skill)}${(statBlock.skillExpertise ?? []).includes(skill) ? ' (expertise)' : ''}`,
  )
  const facts: Array<[string, string]> = [
    ['Proficiency bonus', statBlock.proficiency !== '—' ? signed(statBlock.proficiency) : ''],
    ['Skills', skills.join(', ')],
    ['Tools', (statBlock.toolProficiencies ?? []).map(mechanicLabel).join(', ')],
    ['Languages', (statBlock.languages ?? []).join(', ')],
    ['Background', statBlock.background ?? ''],
  ]
  const vitals: Array<[string, string]> = [
    ['HP', hpLabel(statBlock.hp, viewer)],
    ['AC', statBlock.ac],
    ['Initiative', signed(statBlock.init)],
    ['Speed', statBlock.speed],
  ]

  return (
    <div className="story-sheet story-sheet-character">
      <div className="story-character-head">
        <div className="story-character-portrait">
          <img src={inspector.characterAvatarSrc} alt="" />
        </div>
        <div className="story-character-title">
          <h2 className="story-character-name">{displayCharacter.name}</h2>
          <p className="story-character-line">
            Level {displayCharacter.level} {displayCharacter.ancestryClass}
          </p>
          <div
            className="story-xp"
            role="progressbar"
            aria-label="Experience"
            aria-valuemin={0}
            aria-valuemax={xpProgress.max}
            aria-valuenow={xpProgress.current}
            aria-valuetext={xpProgress.label}
          >
            <span style={{ width: `${xpProgress.percent}%` }} />
          </div>
          <p className="story-xp-label">{xpProgress.label}</p>
        </div>
      </div>

      <dl className="story-vitals">
        {vitals.map(([label, value]) => (
          <div key={label}>
            <dt className="story-label">{label}</dt>
            <dd data-health={label === 'HP' ? hpTone(statBlock.hp, viewer) : undefined}>{value}</dd>
          </div>
        ))}
      </dl>

      {vitality ? (
        <p className="story-sheet-vitality">
          <StoryVitality vitality={vitality} />
        </p>
      ) : null}

      <dl className="story-abilities">
        {statBlock.abilities.map(([label, score, modifier]) => (
          <div key={label} title={ABILITY_NAMES[label]}>
            <dt className="story-label">{label}</dt>
            <dd className="story-ability-modifier">{modifier}</dd>
            <dd className="story-ability-score">{score}</dd>
          </div>
        ))}
      </dl>

      {saves.length || hitDice ? (
        <dl className="story-sheet-lines">
          {saves.length ? (
            <div>
              <dt className="story-label">Saves</dt>
              <dd className="story-saves">
                {saves.map((save, index) => (
                  <Fragment key={save.ability}>
                    {index ? <span className="story-saves-dot" aria-hidden="true">·</span> : null}
                    <span
                      className="story-save"
                      data-proficient={save.proficient || undefined}
                      title={save.proficient ? `${ABILITY_NAMES[save.ability]} save (proficient)` : `${ABILITY_NAMES[save.ability]} save`}
                    >
                      {save.ability} {save.modifier}
                    </span>
                  </Fragment>
                ))}
              </dd>
            </div>
          ) : null}
          {hitDice ? (
            <div>
              <dt className="story-label">Hit dice</dt>
              <dd className="story-hit-dice">{hitDice.current}/{hitDice.max} {hitDice.die}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      {conditions.length ? (
        <div className="story-sheet-conditions" aria-label="Conditions">
          {conditions.map((condition) => (
            <span key={condition} className="story-chip" data-tone="fail">{condition}</span>
          ))}
        </div>
      ) : null}

      <hr className="story-rule" />

      <dl className="story-facts">
        {facts.filter(([, value]) => value).map(([label, value]) => (
          <div key={label}>
            <dt className="story-label">{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>

      <div className="story-sheet-actions" aria-label="Character actions">
        <button
          type="button"
          className="story-link"
          disabled={!inspector.selectedPlayerId}
          onClick={inspector.editSelectedPlayer}
        >
          Edit character
        </button>
        <button
          type="button"
          className="story-link"
          disabled={!inspector.selectedCampaignId || !inspector.playersCount}
          onClick={inspector.loadPlayer}
        >
          Switch character
        </button>
        <button
          type="button"
          className="story-link"
          disabled={!inspector.selectedCampaignId || inspector.createPlayerPending}
          onClick={() => void inspector.createDefaultPlayer()}
        >
          {inspector.createPlayerPending ? 'Creating…' : 'New character'}
        </button>
        <button
          type="button"
          className="story-link"
          aria-expanded={manageOpen}
          onClick={() => setManageOpen((current) => !current)}
        >
          Manage
        </button>
        {manageOpen ? (
          <button
            type="button"
            className="story-link story-link-danger"
            disabled={!inspector.selectedPlayerId}
            onClick={inspector.deleteSelectedPlayer}
          >
            Delete character
          </button>
        ) : null}
      </div>

      {inspector.activePlayers.length ? (
        <>
          <hr className="story-rule" />
          <h3 className="story-label">At the table</h3>
          <ul className="story-table-list">
            {inspector.activePlayers.map((player) => {
              const isViewer = player.id === inspector.selectedPlayerId
              const downed = partyVitality[player.id] ?? null
              return (
                <li key={player.id} data-health={player.health?.tone} data-vitality={downed?.state}>
                  <img src={playerAvatar(player)} alt="" />
                  <span className="story-table-name">{player.character_name || player.name}</span>
                  {downed ? (
                    <StoryVitality vitality={downed} />
                  ) : player.health ? (
                    <span className="story-table-health">{player.health.label}</span>
                  ) : null}
                  <span className="story-table-badge">
                    {isViewer ? 'You' : player.is_typing ? 'typing…' : ''}
                  </span>
                </li>
              )
            })}
          </ul>
        </>
      ) : null}
    </div>
  )
}
