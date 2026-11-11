import { StrictMode, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { createRoot } from 'react-dom/client'
import '../../index.css'
import '../../App.css'
import { SaveImportDialog } from '../../ArchiveDialogs'
import { CampaignActionDialog, StartChapterDialog } from '../../CampaignSessionActionDialogs'
import { CampaignPackImportDialog } from '../../CampaignPackImportDialog'
import DiceRollDialog from '../../DiceRollDialog'
import { PlayerEditDialog } from '../../PlayerEditDialog'
import { ModalShell } from '../../ModalShell'
import { CharacterJoinDialog } from '../../ProfileCharacterDialogs'
import { profileIconSrcForCharacter } from '../../profileIcons'
import type { Campaign, Player, SessionImportMode, SessionSummary, World } from '../../types'
import type { CampaignPackExample, CreateCampaignForm } from '../../useCampaignActions'
import type { PlayerEditDialogState } from '../../usePlayerProfileActions'
import type { RuntimeSettingsForm } from '../../useRuntimeSettings'
import { ShareSessionDialog } from '../../WorkspaceDialogs'
import { AdventuresSheet } from '../sheets/AdventuresSheet'
import { NewAdventureSheet } from '../sheets/NewAdventureSheet'
import { StoryAccountSheet, type AccountAccessProps } from '../sheets/StoryAccountSheet'
import { StorySettingsSheet } from '../sheets/StorySettingsSheet'
import { StoryView } from '../StoryView'
import type { StoryChromeProps } from '../types'
import { makeBoardFixture, makeInspectorFixture } from './fixtures'

/**
 * Development-only harness for the menu and dialog sheets (story-sheets-preview.html),
 * each over the Story stage: `?sheet=settings|account|account-signed-out|adventures|
 * new-adventure|new-adventure-step2|share|next-chapter|confirm-delete|edit-character|
 * choose-character|import-save|dice|pack-import`.
 */

const SHEETS = [
  'settings',
  'account',
  'account-signed-out',
  'adventures',
  'new-adventure',
  'new-adventure-step2',
  'share',
  'next-chapter',
  'confirm-delete',
  'edit-character',
  'choose-character',
  'import-save',
  'dice',
  'pack-import',
] as const
type PreviewSheet = (typeof SHEETS)[number]

const params = new URLSearchParams(window.location.search)
const sheetParam = params.get('sheet')
const previewSheet: PreviewSheet = SHEETS.find((sheet) => sheet === sheetParam) ?? 'settings'

const noop = () => {}
const HOUR_MS = 60 * 60 * 1000
const ago = (hours: number) => new Date(Date.now() - hours * HOUR_MS).toISOString()

const WORLDS: World[] = [
  { world_id: 5, name: 'The Western Roadlands', description: 'Old roads, rain-dark inns and broken watch-posts.', created_at: null },
  { world_id: 8, name: 'Blackwater Reach', description: 'A drowned coast of lantern towers and smugglers.', created_at: null },
]

function campaign(id: number, title: string, worldId: number, sessions: number, hours: number): Campaign {
  return {
    campaign_id: id,
    title,
    description: null,
    world_id: worldId,
    world_name: null,
    created_at: ago(hours + 400),
    updated_at: ago(hours),
    status: 'active',
    is_archived: false,
    current_quest: null,
    location: null,
    session_count: sessions,
    latest_session_id: null,
    latest_activity_at: ago(hours),
  }
}

const CAMPAIGNS: Campaign[] = [
  campaign(37, 'The Lantern Road', 5, 3, 0.4),
  campaign(41, 'Lanterns under Blackwater', 8, 7, 30),
  campaign(12, 'The Road of Unremembered Kings', 5, 1, 24 * 9),
  campaign(19, 'Ashes Beyond the Gate', 5, 12, 24 * 70),
]

const PACKS: CampaignPackExample[] = [
  {
    pack_id: 'roadlands.unremembered_kings',
    title: 'The Road of Unremembered Kings',
    description: '',
    short_description:
      'An empty caravan rolls into a roadside inn at dusk. Its cargo is untouched, and every name in its ledger has been scratched out.',
  },
  {
    pack_id: 'blackwater.lanterns',
    title: 'Lanterns under Blackwater',
    description: '',
    short_description:
      'Harbour intrigue, a drowned archive, and a guide with divided loyalties. The lantern towers along the coast have started to go dark one by one.',
  },
  {
    pack_id: 'middle_earth.shadow_under_eryn_luin',
    title: 'Shadow Under Eryn Luin',
    description: '',
    short_description:
      'Old Dwarf-roads beneath the Blue Mountains have awakened, and a forgotten oath is being exploited.',
  },
]

function player(id: number, characterName: string, name: string, race: string, sex: string, charClass: string, level: number): Player {
  return {
    player_id: id,
    workspace_id: 'owner',
    account_id: null,
    username: null,
    campaign_id: 37,
    name,
    character_name: characterName,
    race,
    sex,
    profile_image: '',
    class_: charClass,
    char_class: charClass,
    level,
    created_at: null,
    updated_at: null,
  }
}

const PLAYERS: Player[] = [
  player(30, 'Arden Vale', 'Danny', 'Human', 'male', 'Fighter', 3),
  player(31, 'Liora Quill', 'Sam', 'Elf', 'female', 'Wizard', 3),
  player(32, 'Mara Fen', 'Alex', 'Halfling', 'female', 'Rogue', 2),
]

const SAVE_SESSION: SessionSummary = {
  session_id: 214,
  campaign_id: 37,
  name: null,
  display_name: 'Chapter 3 · The Drowned Lantern',
  status: 'ended',
  is_current: false,
  is_archived: false,
  chapter_number: 3,
  chapter_recap: '',
  ended_at: ago(20),
  created_at: ago(40),
  updated_at: ago(20),
  latest_activity_at: ago(20),
  turn_count: 48,
  scene_title: null,
  summary_excerpt: '',
  imported: false,
  play_now: false,
}

const EMPTY_RUNTIME_FORM: RuntimeSettingsForm = {
  baseUrl: '',
  workspaceToken: '',
  workspaceName: '',
  workspacePassword: '',
  username: '',
  firstName: '',
  lastName: '',
  password: '',
  recoveryCode: '',
}

function StoryBackdrop() {
  const [board] = useState(() => makeBoardFixture('exploration'))
  const [inspector] = useState(makeInspectorFixture)
  const chrome: StoryChromeProps = {
    connection: 'ready',
    safeMode: true,
    canUseOperatorTools: false,
    ttsAvailable: false,
    ttsEnabled: false,
    onToggleTts: noop,
    isFullscreen: false,
    onToggleFullscreen: noop,
    onOpenConsole: noop,
    onSwitchAdventure: noop,
    onExitToTitle: noop,
    onOpenSettings: noop,
    onOpenAccount: noop,
    endedChapter: null,
  }
  return (
    <StoryView
      board={board}
      inspector={inspector}
      chrome={chrome}
      composer={null}
      drawer={null}
      drawerOpen={false}
      drawerTab="character"
      onDrawerOpenChange={noop}
    />
  )
}

function AccountAccessPreview({ dialogRef }: { dialogRef: RefObject<HTMLElement | null> }) {
  const [form, setForm] = useState<RuntimeSettingsForm>(EMPTY_RUNTIME_FORM)
  const [intent, setIntent] = useState<AccountAccessProps['runtimeAuthIntent']>('login')
  const access: AccountAccessProps = {
    error: '',
    form,
    legacyPasswordSetupRequired: false,
    onAuthIntentChange: setIntent,
    onAuthStepChange: noop,
    onErrorChange: noop,
    onFormChange: setForm,
    onLegacyPasswordSetupRequiredChange: noop,
    onOpenSavedWorkspaceDelete: noop,
    onSelectSavedWorkspace: noop,
    onSubmit: (event) => event.preventDefault(),
    onWorkspaceActionChange: noop,
    onWorkspaceCreateAccessModeChange: noop,
    onWorkspaceJoinMethodChange: noop,
    runtimeAccount: null,
    runtimeAuthIntent: intent,
    runtimeAuthStep: 'account',
    runtimeCreatedWorkspaceToken: '',
    runtimeWorkspaceAction: 'join',
    runtimeWorkspaceCreateAccessMode: 'password',
    runtimeWorkspaceJoinMethod: 'token',
    workspaceId: '',
  }
  return (
    <StoryAccountSheet
      access={access}
      dialogRef={dialogRef}
      onClose={noop}
      onLogOut={noop}
      onSwitchTable={noop}
      view="access"
    />
  )
}

function NewAdventurePreview({ dialogRef, step2 }: { dialogRef: RefObject<HTMLElement | null>; step2: boolean }) {
  const [form, setForm] = useState<CreateCampaignForm>({ title: '', description: '', worldId: '5', worldName: '', packId: '' })
  const opened = useRef(false)
  useEffect(() => {
    if (!step2 || opened.current) return
    opened.current = true
    document.querySelector<HTMLButtonElement>('[aria-label="Begin with a blank page"]')?.click()
  }, [step2])
  return (
    <NewAdventureSheet
      dialogRef={dialogRef}
      error=""
      form={form}
      onClose={noop}
      onSubmit={noop}
      packOptions={PACKS}
      packOptionsPending={false}
      pending={false}
      setForm={setForm}
      worlds={WORLDS}
    />
  )
}

function EditCharacterPreview({ dialogRef }: { dialogRef: RefObject<HTMLElement | null> }) {
  const [dialog, setDialog] = useState<PlayerEditDialogState>({
    player: PLAYERS[0],
    characterName: 'Arden Vale',
    race: 'Human',
    raceSelection: null,
    sex: 'male',
    charClass: 'Fighter',
    backgroundId: '',
    level: '3',
    error: '',
    pending: false,
  })
  if (!dialog) return null
  return (
    <PlayerEditDialog
      auth=""
      baseUrl=""
      dialog={dialog}
      dialogRef={dialogRef}
      onClose={noop}
      onSubmit={(event) => event.preventDefault()}
      setDialog={setDialog}
    />
  )
}

function SaveImportPreview({ dialogRef }: { dialogRef: RefObject<HTMLElement | null> }) {
  const [mode, setMode] = useState<SessionImportMode>('restore')
  const [restoreCharacters, setRestoreCharacters] = useState(true)
  return (
    <SaveImportDialog
      dialog={{
        fileName: 'lantern-road-chapter-3.aidm.json',
        save: {
          format: 'aidm.save',
          version: 2,
          exported_at: ago(20),
          campaign: { id: 37, title: 'The Lantern Road' },
          session: SAVE_SESSION,
          state_snapshot: null,
          session_state: { rolling_summary: '', current_location: null, current_quest: null, memory_snippets: [] },
          log: [],
          characters: [],
          unsettled: { pending_roll: false, active_combat: false },
        },
        mode,
        restoreCharacters,
        error: '',
        pending: false,
      }}
      dialogRef={dialogRef}
      onClose={noop}
      onModeChange={setMode}
      onRestoreCharactersChange={setRestoreCharacters}
      onSubmit={(event) => event.preventDefault()}
    />
  )
}

function PreviewSheetView({ sheet }: { sheet: PreviewSheet }): ReactNode {
  const dialogRef = useRef<HTMLElement | null>(null)
  switch (sheet) {
    case 'settings':
      return (
        <StorySettingsSheet
          dialogRef={dialogRef}
          onClose={noop}
          narration={{ enabled: true, problem: '' }}
          onNarrationChange={noop}
          view="story"
          onViewChange={noop}
          fullscreen={false}
          onFullscreenChange={noop}
          diagnostics={{
            player: 'You are Danny Reichner, playing Arden Vale.',
            table: 'At the Lantern Table.',
            connection: 'Connected to the game server.',
          }}
          onRefresh={noop}
          onReconnect={noop}
          onConnectionSettings={noop}
        />
      )
    case 'account':
      return (
        <StoryAccountSheet
          access={{} as AccountAccessProps}
          account={{ name: 'Danny Reichner', table: 'the Lantern Table', role: 'player' }}
          dialogRef={dialogRef}
          onClose={noop}
          onLogOut={noop}
          onSwitchTable={noop}
          view="summary"
        />
      )
    case 'account-signed-out':
      return <AccountAccessPreview dialogRef={dialogRef} />
    case 'adventures':
      return (
        <AdventuresSheet
          campaigns={CAMPAIGNS}
          chapterMeta={{}}
          currentCampaignId={37}
          dialogRef={dialogRef}
          onClose={noop}
          onContinue={noop}
          onImportPack={noop}
          onNewAdventure={noop}
          onOpenArchive={noop}
          onPlayReadyMade={noop}
          worldNameById={new Map(WORLDS.map((world) => [world.world_id, world.name]))}
        />
      )
    case 'new-adventure':
    case 'new-adventure-step2':
      return <NewAdventurePreview dialogRef={dialogRef} step2={sheet === 'new-adventure-step2'} />
    case 'share':
      return (
        <ShareSessionDialog
          dialogRef={dialogRef}
          onClose={noop}
          onCopy={noop}
          url="https://play.aidm.example/?campaign=37&session=214&join=lantern"
        />
      )
    case 'next-chapter':
      return (
        <StartChapterDialog
          canForce={false}
          dialog={{ fresh: false, force: false, unsettled: false, error: '', pending: false }}
          dialogRef={dialogRef}
          onClose={noop}
          onForceChange={noop}
          onFreshChange={noop}
          onSubmit={(event) => event.preventDefault()}
        />
      )
    case 'confirm-delete':
      return (
        <CampaignActionDialog
          dialog={{
            mode: 'delete',
            campaign: CAMPAIGNS[1],
            title: CAMPAIGNS[1].title,
            description: '',
            error: '',
            pending: false,
          }}
          dialogRef={dialogRef}
          onClose={noop}
          onDescriptionChange={noop}
          onSubmit={(event) => event.preventDefault()}
          onTitleChange={noop}
        />
      )
    case 'edit-character':
      return <EditCharacterPreview dialogRef={dialogRef} />
    case 'choose-character':
      return (
        <CharacterJoinDialog
          campaignTitle="The Lantern Road"
          dialogRef={dialogRef}
          onClose={noop}
          onCreateCharacter={noop}
          onJoinPlayer={noop}
          open
          players={PLAYERS}
          portraitSrcForPlayer={(item) => profileIconSrcForCharacter({ race: item.race, sex: item.sex }) ?? ''}
        />
      )
    case 'import-save':
      return <SaveImportPreview dialogRef={dialogRef} />
    case 'pack-import':
      return (
        <ModalShell
          size="wide"
          closeLabel="Close campaign pack import"
          description="Check an adventure pack file, or forge one from a premise, then create the adventure."
          dialogRef={dialogRef}
          labelledBy="campaign-pack-import-title"
          onClose={noop}
          title="Import Campaign Pack"
        >
          <CampaignPackImportDialog auth="" baseUrl="" onClose={noop} onImported={async () => {}} pushError={noop} />
        </ModalShell>
      )
    case 'dice':
      return (
        <DiceRollDialog
          die="d20"
          result={16}
          rolls={[16]}
          mode="normal"
          modifier={3}
          total={19}
          targetLabel="Tovin Bell"
          rollKey={1}
          status="resolved"
          dialogRef={dialogRef}
          onCancel={noop}
          onComplete={noop}
          onRetry={noop}
        />
      )
  }
}

export function SheetsPreview() {
  return (
    <>
      <StoryBackdrop />
      <PreviewSheetView sheet={previewSheet} />
    </>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SheetsPreview />
  </StrictMode>,
)
