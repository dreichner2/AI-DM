import { isRecord, numberValue, stringValue, type WorldMerchantSummary } from '../gameSelectors'
import type { GameplayTradeGood } from '../gameplayControlState'
import type { TimelineEntry } from '../types'

/**
 * Coin arithmetic and server refusals for the Trade section. Prices always come
 * from the server; these helpers only add them up the way commerce.py does.
 */

const COIN_VALUES_CP: Record<string, number> = { pp: 1000, gp: 100, ep: 50, sp: 10, cp: 1 }
const COIN_ORDER = ['pp', 'gp', 'ep', 'sp', 'cp']
const CHANGE_COINS = ['gp', 'sp', 'cp']

export type Coins = Record<string, number>

export function copperValue(coins: Coins) {
  return Object.entries(coins).reduce((total, [coin, amount]) => total + (COIN_VALUES_CP[coin] ?? 0) * amount, 0)
}

/** "12 gp 5 sp": the largest coins first, empty coins left out. */
export function coinLabel(coins: Coins) {
  return COIN_ORDER.filter((coin) => coins[coin] > 0).map((coin) => `${coins[coin]} ${coin}`).join(' ') || '0 cp'
}

function coinsForCopper(amount: number): Coins {
  let remaining = Math.max(0, Math.floor(amount))
  const coins: Coins = {}
  for (const coin of CHANGE_COINS) {
    const count = Math.floor(remaining / COIN_VALUES_CP[coin])
    remaining -= count * COIN_VALUES_CP[coin]
    if (count) coins[coin] = count
  }
  return coins
}

function nameKey(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
}

/**
 * What the merchant pays for one of the item: the item's listed price (or, when
 * the server sends none, the merchant's own price for the same goods) times the
 * merchant's buy rate, rounded down to the copper. Null when nothing prices it.
 */
export function sellOffer(good: GameplayTradeGood, merchant: WorldMerchantSummary): Coins | null {
  const listed = good.price ?? merchant.stock.find((entry) => nameKey(entry.name) === nameKey(good.name))?.price ?? null
  if (!listed) return null
  return coinsForCopper(copperValue(listed) * merchant.buyRate)
}

export function tradeLineKey(action: 'buy' | 'sell', merchantId: string, reference: string) {
  return `${action}:${merchantId}:${reference}`
}

const REJECTED_PREFIX_RE = /^.*?\brejected:\s*/i

function rejectedShopReasons(entry: TimelineEntry) {
  const stateLog = isRecord(entry.metadata.state_log) ? entry.metadata.state_log : {}
  const lines = Array.isArray(stateLog.lines) ? stateLog.lines.filter(isRecord) : []
  return lines
    .filter((line) => stringValue(line.status) === 'rejected' && stringValue(line.changeType).startsWith('shop.'))
    .map((line) => stringValue(line.message).replace(REJECTED_PREFIX_RE, ''))
    .filter(Boolean)
}

/**
 * The server's refusal of the latest attempt at each trade line, keyed by
 * `tradeLineKey`. An attempt is a player turn carrying a shop intent; its
 * refusal is the rejected shop line in that turn's state update.
 */
export function tradeRefusals(entries: TimelineEntry[]): Map<string, string> {
  const latestTurnByLine = new Map<string, number>()
  const refusalsByTurn = new Map<number, string>()
  entries.forEach((entry) => {
    const turnId = numberValue(entry.metadata.turn_id)
    if (turnId === null) return
    const intent = isRecord(entry.metadata.action_intent) ? entry.metadata.action_intent : null
    const shop = intent && stringValue(intent.kind) === 'shop' && isRecord(intent.shop) ? intent.shop : null
    if (entry.role === 'player' && shop) {
      const action = stringValue(shop.action) === 'sell' ? 'sell' : 'buy'
      const reference = stringValue(action === 'buy' ? shop.stock_id : shop.item_id)
      latestTurnByLine.set(tradeLineKey(action, stringValue(shop.merchant_id), reference), turnId)
      return
    }
    if (entry.role === 'system') {
      const [reason] = rejectedShopReasons(entry)
      if (reason) refusalsByTurn.set(turnId, reason)
    }
  })
  const refusals = new Map<string, string>()
  latestTurnByLine.forEach((turnId, key) => {
    const reason = refusalsByTurn.get(turnId)
    if (reason) refusals.set(key, reason)
  })
  return refusals
}
