// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { LEGACY_PASSWORD_SETUP_MESSAGE } from './useRuntimeSettings'
import {
  App,
  appTestState,
  installMatchMediaMock,
  renderLoadedApp,
  setupAppTest,
  teardownAppTest,
  toggleAdminToolsViaComposerLabel,
} from './App.testHarness'

describe('App runtime and workspace access', () => {
  beforeEach(setupAppTest)
  afterEach(teardownAppTest)

  it('opens table settings from the mobile top bar gear', async () => {
    installMatchMediaMock(true)
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Open table settings' }))

    const dialog = await screen.findByRole('dialog', { name: 'Log In' })
    expect(within(dialog).getByText('Access')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open table settings' })).toBeInTheDocument()
  }, 10_000)

  it('does not show the mobile table settings gear on desktop', async () => {
    await renderLoadedApp()

    expect(screen.queryByRole('button', { name: 'Open table settings' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change table access' })).toBeInTheDocument()
  })
  it('prompts for an account when the public app requires a table token', async () => {
    appTestState.requiredAuthToken = 'shared-token'
    localStorage.setItem('aidm:selectedPlayerId', '30')

    render(<App />)

    let dialog = await screen.findByRole('dialog', { name: 'Log In' })
    expect(screen.queryByLabelText('Scene music player')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Backend URL')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Table Token')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign Up' }))

    const usernameInput = within(dialog).getByLabelText('Username')
    await waitFor(() => expect(usernameInput).toHaveFocus())
    fireEvent.change(usernameInput, { target: { value: 'Danny' } })
    fireEvent.change(within(dialog).getByLabelText('First Name'), { target: { value: 'Danny' } })
    fireEvent.change(within(dialog).getByLabelText('Last Name'), { target: { value: 'Reichner' } })
    fireEvent.change(within(dialog).getByLabelText('Password'), { target: { value: 'secret' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))

    dialog = await screen.findByRole('dialog', { name: 'Join Table' })
    expect(screen.queryByLabelText('Scene music player')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Username')).not.toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Table Token'), { target: { value: 'shared-token' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Join Table' }))

    await screen.findByRole('heading', { name: /Session Alpha/i })
    expect(await screen.findByLabelText('Scene music player')).toBeInTheDocument()
    expect(sessionStorage.getItem('aidm:authToken')).toBe('account-token')
    expect(sessionStorage.getItem('aidm:workspaceToken')).toBe('shared-token')
    expect(screen.queryByRole('dialog', { name: 'Join Table' })).not.toBeInTheDocument()
    await waitFor(() =>
      expect(screen.queryByText('Table token required. Enter the table token to connect.')).not.toBeInTheDocument(),
    )
    expect(screen.queryByText('Player load failed: Missing or invalid workspace token.')).not.toBeInTheDocument()
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/campaigns',
        }),
        expect.objectContaining({
          method: 'GET',
          path: '/api/campaigns/10/workspace',
        }),
        expect.objectContaining({
          method: 'GET',
          path: '/api/players/30',
        }),
      ]),
    )
  })

  it('switches global providers from another table for owner admins', async () => {
    appTestState.requiredAuthToken = 'owner-token'
    sessionStorage.setItem('aidm:authToken', 'account-token')
    localStorage.setItem('aidm:workspaceId', 'friend')

    render(<App />)

    const providerSelect = await screen.findByTitle('Current runtime provider')
    await waitFor(() => expect(providerSelect).toBeEnabled())

    fireEvent.change(providerSelect, { target: { value: 'fallback' } })

    await waitFor(() => expect(providerSelect).toHaveValue('fallback'))
    expect(
      await screen.findByText('Fallback DM active. Ask the table operator to restore the live provider.'),
    ).toBeInTheDocument()
    const fullNoticeToggle = screen.getByLabelText('Read full Safe Mode notice')
    fireEvent.click(fullNoticeToggle)
    expect(
      within(screen.getByRole('note')).getByText(
        'Recovery guidance: Fallback DM active. Ask the table operator to restore the live provider.',
      ),
    ).toBeVisible()
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'PATCH',
          path: '/api/llm/config',
          authorization: 'Bearer account-token',
          workspaceToken: null,
          workspaceIdHeader: 'owner',
          body: {
            provider: 'fallback',
            model: 'deterministic-v1',
            persist: true,
          },
        }),
      ]),
    )
    expect(screen.queryByText(/Runtime switch failed/i)).not.toBeInTheDocument()
  })

  it('keeps non-actionable local private status out of the player alert bar', async () => {
    await renderLoadedApp()

    expect(screen.queryByLabelText('Beta runtime notices')).not.toBeInTheDocument()
    expect(screen.queryByText('Local/Private')).not.toBeInTheDocument()
    expect(screen.queryByText('Auth disabled.')).not.toBeInTheDocument()
  })

  it('shows the complete authoring shell to operators', async () => {
    appTestState.health.auth_required = true
    appTestState.requiredAuthToken = 'account-token'
    sessionStorage.setItem('aidm:authToken', 'account-token')
    localStorage.setItem('aidm:workspaceId', 'owner')

    render(<App />)
    fireEvent.click(await screen.findByRole('button', { name: 'Choose Smoke Campaign' }))
    await screen.findByRole('heading', { name: /Session Alpha/i })
    await waitFor(() => expect(screen.getAllByText('Ember').length).toBeGreaterThan(0))

    expect(screen.getByRole('tab', { name: 'Bestiary' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Ops' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rename selected campaign' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open campaign archive' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Manage worlds' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Import campaign pack' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add campaign' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rename selected session' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open session archive' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start session' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Director Commentary' })).toBeInTheDocument()
    expect(screen.getByText('Operator')).toBeInTheDocument()

    const inspector = screen.getByRole('tablist', { name: 'Inspector panels' })
    expect(within(inspector).getByRole('tab', { name: 'Memory' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Recent Memory/i })).toBeInTheDocument()
    fireEvent.click(within(inspector).getByRole('tab', { name: 'Map' }))
    expect(screen.getByRole('heading', { name: 'Map Details' })).toBeInTheDocument()
    expect(screen.getByLabelText('Segment title')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add segment' })).toBeInTheDocument()

    toggleAdminToolsViaComposerLabel()
    expect(screen.getByRole('button', { name: 'Admin mode' })).toBeInTheDocument()
  })

  it('hides Bestiary and Ops inspector surfaces from non-operator players', async () => {
    appTestState.health.auth_required = true
    appTestState.requiredAuthToken = 'player-token'
    appTestState.mapsByCampaign = {
      10: [
        {
          map_id: 40,
          world_id: 5,
          campaign_id: 10,
          title: 'Revealed Crossing',
          description: 'A crossing the party has discovered.',
          map_data: {},
          visibility: 'player',
          created_at: null,
          updated_at: null,
        },
        {
          map_id: 41,
          world_id: 5,
          campaign_id: 10,
          title: 'DM_ONLY_STALE_MAP',
          description: 'A cached DM map must fail closed after the role changes.',
          map_data: { marker: 'DM_ONLY_STALE_DATA' },
          visibility: 'dm',
          created_at: null,
          updated_at: null,
        },
      ],
    }
    appTestState.segmentsByCampaign = {
      10: [
        {
          segment_id: 50,
          campaign_id: 10,
          title: 'Revealed Milestone',
          description: 'The party has already crossed the broken bridge.',
          trigger_condition: null,
          tags: null,
          external_id: null,
          source: 'runtime',
          source_pack_id: null,
          metadata: {},
          is_triggered: true,
          created_at: null,
          updated_at: null,
        },
      ],
    }
    sessionStorage.setItem('aidm:authToken', 'player-token')
    localStorage.setItem('aidm:workspaceId', 'owner')

    render(<App />)

    const inspector = await screen.findByRole('tablist', { name: 'Inspector panels' })
    await waitFor(() => {
      expect(within(inspector).queryByRole('tab', { name: 'Bestiary' })).not.toBeInTheDocument()
      expect(within(inspector).queryByRole('tab', { name: 'Ops' })).not.toBeInTheDocument()
    })
    expect(within(inspector).getByRole('tab', { name: 'Party' })).toBeInTheDocument()
    expect(within(inspector).getByRole('tab', { name: 'Memory' })).toBeInTheDocument()
    expect(within(inspector).queryByRole('tab', { name: 'Canon' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Recent Memory/i })).toBeInTheDocument()

    for (const name of [
      'Rename selected campaign',
      'Open campaign archive',
      'Delete selected campaign',
      'Manage worlds',
      'Import campaign pack',
      'Add campaign',
      'Rename selected session',
      'Open session archive',
      'Delete selected session',
      'Start session',
      'Director Commentary',
    ]) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
    }
    expect(screen.queryByText('Operator')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download session Chronicle' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Import' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Share' })).toBeInTheDocument()

    toggleAdminToolsViaComposerLabel()
    expect(screen.queryByRole('button', { name: 'Admin mode' })).not.toBeInTheDocument()

    expect(screen.queryByRole('button', { name: 'Create Campaign' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Choose Smoke Campaign' }))
    await screen.findByRole('heading', { name: /Session Alpha/i })
    fireEvent.click(within(inspector).getByRole('tab', { name: 'Map' }))
    expect(screen.getByRole('heading', { name: 'Revealed Crossing' })).toBeInTheDocument()
    expect(screen.queryByText('DM_ONLY_STALE_MAP')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Map Details' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create map' })).not.toBeInTheDocument()
    expect(screen.getByText('Revealed Milestone')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Set active' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Segment title')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add segment' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Session menu' }))
    const sessionMenu = screen.getByRole('menu', { name: 'Session menu' })
    expect(within(sessionMenu).getByRole('menuitem', { name: 'Download session Chronicle' })).toBeInTheDocument()
    expect(within(sessionMenu).getByRole('menuitem', { name: 'Download campaign Chronicle' })).toBeInTheDocument()
    expect(within(sessionMenu).queryByRole('menuitem', { name: 'Rename session' })).not.toBeInTheDocument()
    expect(within(sessionMenu).queryByRole('menuitem', { name: 'Delete session' })).not.toBeInTheDocument()
  })

  it('keeps beta information available from the account menu without stale operator guidance', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Account' }))
    const accountMenu = await screen.findByRole('menu', { name: 'Account options' })
    const notesToggle = within(accountMenu).getByRole('menuitem', { name: 'Beta information' })
    expect(notesToggle).toHaveAttribute('aria-expanded', 'false')
    expect(notesToggle).toHaveAttribute('aria-controls', 'beta-runtime-information')
    fireEvent.click(notesToggle)

    const notes = await screen.findByRole('region', { name: 'Beta information' })
    expect(notesToggle).toHaveAttribute('aria-expanded', 'true')
    expect(within(notes).getByRole('heading', { name: 'Beta information' })).toBeInTheDocument()
    expect(
      within(notes).getByText(/Live DM availability depends on the provider configured for the table/i),
    ).toBeInTheDocument()
    expect(within(notes).queryByText(/Hosted cookie auth, CSRF, Socket.IO, and restore behavior/i)).not.toBeInTheDocument()
    expect(within(notes).queryByText(/hidden authored content/i)).not.toBeInTheDocument()
    expect(within(notes).queryByText(/support bundles can expose/i)).not.toBeInTheDocument()

    fireEvent.click(within(notes).getByRole('button', { name: 'Close beta information' }))

    await waitFor(() => expect(screen.queryByRole('region', { name: 'Beta information' })).not.toBeInTheDocument())
    expect(notesToggle).toHaveAttribute('aria-expanded', 'false')
    await waitFor(() => expect(notesToggle).toHaveFocus())
  })

  it('keeps unavailable TTS in its accessible control instead of the global alert bar', async () => {
    appTestState.ttsConfig.configured = false

    await renderLoadedApp()

    expect(screen.queryByLabelText('Beta runtime notices')).not.toBeInTheDocument()
    const narrationControl = screen.getByRole('button', {
      name: 'Narration unavailable; Deepgram is not configured',
    })
    expect(narrationControl).toHaveAttribute('aria-pressed', 'false')
    expect(narrationControl).toHaveAttribute('data-tts-configuration', 'unavailable')
    expect(narrationControl).toHaveAttribute(
      'title',
      'Unavailable: Deepgram narration is unavailable because the backend is not configured',
    )
    fireEvent.click(narrationControl)
    expect(screen.getAllByText('Deepgram TTS is not configured on the backend.').length).toBeGreaterThan(0)
  })

  it('distinguishes a failed TTS configuration check from loading and credential absence', async () => {
    appTestState.ttsConfigFetchError = 'Temporary configuration lookup failure.'

    await renderLoadedApp()

    expect(screen.queryByLabelText('Beta runtime notices')).not.toBeInTheDocument()
    const narrationControl = screen.getByRole('button', {
      name: 'Narration status unavailable; configuration check failed',
    })
    expect(narrationControl).toHaveAttribute('aria-pressed', 'false')
    expect(narrationControl).toHaveAttribute('data-tts-configuration', 'error')
    expect(narrationControl).toHaveAttribute(
      'title',
      'Status unavailable: Could not check Deepgram narration configuration; refresh to retry',
    )
    fireEvent.click(narrationControl)
    expect(
      screen.getAllByText('Narration configuration could not be checked. Refresh to retry.').length,
    ).toBeGreaterThan(0)
  })

  it('surfaces missing live provider configuration in beta runtime notices', async () => {
    if (appTestState.health.llm) {
      appTestState.health.llm.configured = false
    }

    await renderLoadedApp()

    const notices = await screen.findByLabelText('Beta runtime notices')
    expect(within(notices).getByText('Provider Key')).toBeInTheDocument()
    expect(
      within(notices).getByText(
        'Live DM is unavailable. Ask the table operator to configure the selected provider.',
      ),
    ).toBeInTheDocument()
  })

  it('hides process-local instructions for a single worker', async () => {
    appTestState.runtime.runtime_scope = 'process'
    appTestState.runtime.worker_count = 1
    appTestState.runtime.restart_required_for_other_workers = true

    await renderLoadedApp()

    expect(screen.queryByText(/Process-local/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/restart workers/i)).not.toBeInTheDocument()
  })

  it('shows process-local synchronization only to workspace admins when multiple workers need it', async () => {
    appTestState.runtime.runtime_scope = 'process'
    appTestState.runtime.worker_count = 2
    appTestState.runtime.restart_required_for_other_workers = true

    await renderLoadedApp()

    expect(screen.queryByLabelText('Beta runtime notices')).not.toBeInTheDocument()
    expect(screen.getByText('Process-local · restart workers')).toHaveAttribute(
      'title',
      'Provider changes apply to this backend process; restart the other workers to synchronize them.',
    )
  })

  it('hides process-local synchronization from non-admin players', async () => {
    appTestState.health.auth_required = true
    appTestState.requiredAuthToken = 'player-token'
    appTestState.runtime.runtime_scope = 'process'
    appTestState.runtime.worker_count = 2
    appTestState.runtime.restart_required_for_other_workers = true
    sessionStorage.setItem('aidm:authToken', 'player-token')
    localStorage.setItem('aidm:workspaceId', 'owner')

    render(<App />)

    await waitFor(() =>
      expect(appTestState.fetchCalls).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ method: 'GET', path: '/api/capabilities' }),
          expect.objectContaining({ method: 'GET', path: '/api/campaigns' }),
        ]),
      ),
    )

    expect(screen.queryByText('Process-local · restart workers')).not.toBeInTheDocument()
    expect(
      appTestState.fetchCalls.some(
        (call) => call.method === 'GET' && call.path === '/api/llm/config',
      ),
    ).toBe(false)
    expect(
      appTestState.fetchCalls.some(
        (call) => call.method === 'GET' && call.path === '/api/beta/summary',
      ),
    ).toBe(false)
  })

  it('keeps restored legacy passwordless sessions in password setup', async () => {
    appTestState.requiredAuthToken = 'owner-token'
    sessionStorage.setItem('aidm:authToken', 'legacy-account-token')
    sessionStorage.setItem('aidm:workspaceToken', 'owner-token')
    localStorage.setItem('aidm:workspaceId', 'owner')

    render(<App />)

    const dialog = await screen.findByRole('dialog', { name: 'Sign Up' })
    expect(within(dialog).getAllByText(LEGACY_PASSWORD_SETUP_MESSAGE)).not.toHaveLength(0)
    expect(within(dialog).queryByLabelText('First Name')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Last Name')).not.toBeInTheDocument()
    expect(within(dialog).getByLabelText('Recovery Code')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('New Password')).toBeInTheDocument()
    expect(screen.queryByLabelText('Scene music player')).not.toBeInTheDocument()
    await waitFor(() => expect(sessionStorage.getItem('aidm:workspaceToken')).toBeNull())
    expect(localStorage.getItem('aidm:workspaceId')).toBeNull()
  })

  it('opens account auth from the backend gear when no account is active', async () => {
    await renderLoadedApp()
    expect(screen.getByLabelText('Scene music player')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Change table access' }))

    const dialog = await screen.findByRole('dialog', { name: 'Log In' })
    expect(screen.queryByLabelText('Scene music player')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Backend URL')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Table Token')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Sign Up' })).toBeInTheDocument()
  })

  it('opens table auth from the backend gear when an account is active', async () => {
    sessionStorage.setItem('aidm:authToken', 'account-token')
    sessionStorage.setItem('aidm:workspaceToken', 'old-workspace')
    sessionStorage.setItem(
      'aidm:account',
      JSON.stringify({
        accountId: 1,
        username: 'danny',
        displayName: 'Danny Reichner',
        workspaceId: 'owner',
        workspaceRole: 'admin',
        isWorkspaceAdmin: true,
        workspaces: [
          {
            workspace_id: 'owner',
            workspace_role: 'admin',
            is_workspace_admin: true,
            created_at: null,
            updated_at: null,
          },
        ],
      }),
    )
    localStorage.setItem('aidm:workspaceId', 'owner')
    window.history.replaceState(null, '', '/?campaign=10&session=20')

    render(<App />)
    await screen.findByRole('button', { name: 'Change table access' })
    await screen.findByText('Test')
    expect(screen.getByText('Table')).toBeInTheDocument()
    expect(screen.queryByText('Same origin')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Change table access' }))

    const dialog = await screen.findByRole('dialog', { name: 'Join Table' })
    expect(within(dialog).queryByLabelText('Backend URL')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('group', { name: 'Saved tables' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Test admin' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Delete Test' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Remove Friend Table' })).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Table Token')).toHaveValue('old-workspace')

    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove Friend Table' }))
    let confirmDialog = await screen.findByRole('dialog', { name: 'Remove Saved Table' })
    expect(within(confirmDialog).getByText('Friend Table')).toBeInTheDocument()
    expect(within(confirmDialog).getByText('This removes the table from your saved tables only.')).toBeInTheDocument()
    fireEvent.click(within(confirmDialog).getByRole('button', { name: 'Remove' }))
    await waitFor(() =>
      expect(appTestState.fetchCalls).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            method: 'DELETE',
            path: '/api/accounts/workspaces/friend',
          }),
        ]),
      ),
    )
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Remove Saved Table' })).not.toBeInTheDocument())

    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete Test' }))
    confirmDialog = await screen.findByRole('dialog', { name: 'Delete Table' })
    expect(within(confirmDialog).getByText('Test')).toBeInTheDocument()
    expect(
      within(confirmDialog).getByText('This permanently deletes the table for everyone. This cannot be undone.'),
    ).toBeInTheDocument()
    fireEvent.click(within(confirmDialog).getByRole('button', { name: 'Delete Table' }))
    await waitFor(() =>
      expect(appTestState.fetchCalls).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            method: 'DELETE',
            path: '/api/accounts/workspaces/owner',
          }),
        ]),
      ),
    )
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Delete Table' })).not.toBeInTheDocument())
  })

  it('creates a token table and warns that the generated token is only shown once', async () => {
    sessionStorage.setItem('aidm:authToken', 'account-token')
    sessionStorage.setItem(
      'aidm:account',
      JSON.stringify({
        accountId: 1,
        username: 'danny',
        displayName: 'Danny Reichner',
        workspaceId: 'owner',
        workspaceRole: 'admin',
        isWorkspaceAdmin: true,
        workspaces: [
          {
            workspace_id: 'owner',
            workspace_role: 'admin',
            is_workspace_admin: true,
            created_at: null,
            updated_at: null,
          },
        ],
      }),
    )
    localStorage.setItem('aidm:workspaceId', 'owner')

    render(<App />)
    await screen.findByRole('button', { name: 'Change table access' })

    fireEvent.click(screen.getByRole('button', { name: 'Change table access' }))
    let dialog = await screen.findByRole('dialog', { name: 'Join Table' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))

    dialog = await screen.findByRole('dialog', { name: 'Create Table' })
    fireEvent.change(within(dialog).getByLabelText('Table Name'), { target: { value: 'Token Table' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Token' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create Table' }))

    dialog = await screen.findByRole('dialog', { name: 'Save Table Token' })
    expect(within(dialog).getByLabelText('Generated table token')).toHaveValue('generated-token-for-Token_Table')
    expect(within(dialog).getByText('You will not be able to view it after you leave this page.')).toBeInTheDocument()
    expect(sessionStorage.getItem('aidm:workspaceToken')).toBeNull()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Done' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Save Table Token' })).not.toBeInTheDocument())
  })
  it('clears stale owner selections after connecting to an empty auth workspace', async () => {
    appTestState.requiredAuthToken = 'aidan_test'
    appTestState.campaigns = []
    appTestState.worlds = []
    appTestState.sessionsByCampaign = {}
    appTestState.playersByCampaign = {}
    appTestState.mapsByCampaign = {}
    appTestState.segmentsByCampaign = {}
    appTestState.sessionLogs = {}
    appTestState.sessionStates = {}
    appTestState.playerDetails = {}

    render(<App />)

    let dialog = await screen.findByRole('dialog', { name: 'Log In' })
    fireEvent.change(within(dialog).getByLabelText('Username'), { target: { value: 'Aidan' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))

    dialog = await screen.findByRole('dialog', { name: 'Join Table' })
    fireEvent.change(within(dialog).getByLabelText('Table Token'), { target: { value: 'aidan_test' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Join Table' }))

    await screen.findByText('No campaigns match.')
    await waitFor(() => {
      expect(screen.queryByText(/Workspace load failed:/)).not.toBeInTheDocument()
      expect(screen.queryByText(/Session refresh failed:/)).not.toBeInTheDocument()
      expect(screen.queryByText(/Player load failed:/)).not.toBeInTheDocument()
    })
    await waitFor(() => {
      const params = new URLSearchParams(window.location.search)
      expect(params.has('campaign')).toBe(false)
      expect(params.has('session')).toBe(false)
    })
  })
})
