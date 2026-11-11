import { lazyModule } from '../lazyModule'

// Screens a player opens on demand, kept out of the start-up bundle. Each is
// fetched ahead when the player reaches for the control that opens it.
export const accountSheetChunk = lazyModule(() => import('./sheets/StoryAccountSheet'))
export const settingsSheetChunk = lazyModule(() => import('./sheets/StorySettingsSheet'))
export const sessionDialogsChunk = lazyModule(() => import('../CampaignSessionActionDialogs'))
export const drawerChunk = lazyModule(() => import('./StoryDrawer'))
/** The end of a chapter, coming back, the ending's last sections and the leaving question. */
export const returnChunk = lazyModule(() => import('./StoryReturn'))
export const drawerSectionChunks = {
  character: lazyModule(() => import('./drawer/CharacterSection')),
  pack: lazyModule(() => import('./drawer/PackSection')),
  magic: lazyModule(() => import('./drawer/MagicSection')),
  journal: lazyModule(() => import('./drawer/JournalSection')),
}

/** The drawer and its sections; the map waits until its tab is opened. */
export function preloadStoryDrawer() {
  drawerChunk.preload()
  Object.values(drawerSectionChunks).forEach((chunk) => chunk.preload())
}
