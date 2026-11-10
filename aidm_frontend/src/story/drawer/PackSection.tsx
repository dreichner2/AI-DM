import type { InspectorPanelProps } from '../../InspectorPanel'

type PackSectionProps = {
  inspector: InspectorPanelProps
}

export function PackSection({ inspector }: PackSectionProps) {
  const items = [...inspector.inventoryRows].sort(
    (left, right) => Number(right.equipped) - Number(left.equipped),
  )
  return (
    <div className="story-sheet story-sheet-pack">
      <p className="story-sheet-meta">
        {inspector.inventoryGoldLabel} · {inspector.inventoryWeightLabel}
      </p>
      {items.length ? (
        <ul className="story-pack-list" aria-label="Pack">
          {items.map((item, index) => {
            const count = Number.parseInt(item.count, 10) || 1
            const pending = inspector.equipmentPendingItemKey === (item.id || item.item)
            return (
              <li key={`${item.id || item.item}-${index}`} data-equipped={item.equipped || undefined}>
                <span className="story-pack-name">
                  {item.item}
                  {count > 1 ? <span className="story-pack-count"> ×{count}</span> : null}
                </span>
                {item.equippable ? (
                  <button
                    type="button"
                    className="story-link story-pack-equip"
                    aria-pressed={item.equipped}
                    disabled={pending}
                    onClick={() => void inspector.toggleInventoryEquipment(item)}
                  >
                    {pending ? (
                      <>
                        <span className="story-dots" aria-hidden="true"><i /><i /><i /></span>
                        <span className="story-sr-only">Updating {item.item}</span>
                      </>
                    ) : (
                      <>
                        {item.equipped ? 'Equipped' : 'Equip'}{' '}
                        <span className="story-sr-only">{item.item}</span>
                      </>
                    )}
                  </button>
                ) : (
                  <span aria-hidden="true" />
                )}
                <span className="story-pack-weight">{item.weight}</span>
              </li>
            )
          })}
        </ul>
      ) : (
        <p className="story-sheet-empty">Your pack is empty.</p>
      )}
    </div>
  )
}
