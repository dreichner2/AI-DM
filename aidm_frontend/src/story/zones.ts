import type { WorldZoneSummary } from '../gameSelectors'

/**
 * The rooms and areas of a place as the Story view reads them: where the viewer stands, which zone each person is
 * in, and how an offered action names a zone next door ("Talk to Mara Reedwake · common room").
 */

/** "the common room" for "The Common Room"; a name without a leading article is kept as it is. */
export function zonePhrase(name: string) {
  return name.toLowerCase().startsWith('the ') ? name.toLowerCase() : name
}

/** "common room" for "The Common Room": how an action next door is marked. */
export function zoneMark(name: string) {
  const lowered = name.toLowerCase().startsWith('the ') ? name.slice(4).toLowerCase() : name
  return lowered.trim()
}

/** The zone the viewer stands in; null for a place that is one area. */
export function currentZone(zones: WorldZoneSummary[] | undefined) {
  return zones?.find((zone) => zone.current) ?? null
}

/** The zone a person (or hero) with this id stands in. */
export function zoneOfPerson(zones: WorldZoneSummary[] | undefined, id: string) {
  return zones?.find((zone) => zone.here.some((person) => person.id === id)) ?? null
}

/**
 * Where something in a zone is from the viewer: `here` (their own zone, or a place without zones), `next` (a zone
 * next door: the action steps in first, and its label is marked) or `away` (further off: not offered).
 */
export function zoneReach(zones: WorldZoneSummary[] | undefined, zoneId: string | undefined) {
  if (!zones?.length || !zoneId) return 'here'
  const zone = zones.find((candidate) => candidate.id === zoneId)
  if (!zone || zone.current) return 'here'
  return zone.adjacent ? 'next' : 'away'
}

/** "Talk to Mara Reedwake · common room" for an action on someone or something next door. */
export function markedLabel(label: string, zones: WorldZoneSummary[] | undefined, zoneId: string | undefined) {
  const zone = zoneReach(zones, zoneId) === 'next' ? zones?.find((candidate) => candidate.id === zoneId) : undefined
  return zone ? `${label} · ${zoneMark(zone.name)}` : label
}

/** Who stands in a zone by name, the viewer first as "you". */
export function zonePeople(zone: WorldZoneSummary, viewerId: string) {
  return [
    ...zone.here.filter((person) => person.id === viewerId).map(() => 'you'),
    ...zone.here.filter((person) => person.id !== viewerId).map((person) => person.name),
  ]
}

/** The zones as the where-line reads them: the viewer's own, then those next door, then the rest the party knows. */
export function zonesInReadingOrder(zones: WorldZoneSummary[]) {
  const rank = (zone: WorldZoneSummary) => (zone.current ? 0 : zone.adjacent ? 1 : 2)
  return [...zones].sort((left, right) => rank(left) - rank(right))
}
