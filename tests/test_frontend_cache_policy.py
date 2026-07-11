from __future__ import annotations

from flask import Flask

from aidm_server.main import configure_frontend_routes


def test_frontend_cache_policy_distinguishes_shell_bundles_and_media(tmp_path):
    dist_dir = tmp_path / 'dist'
    assets_dir = dist_dir / 'assets'
    profile_icons_dir = dist_dir / 'profile-icons'
    music_dir = dist_dir / 'music'
    assets_dir.mkdir(parents=True)
    profile_icons_dir.mkdir()
    music_dir.mkdir()
    (dist_dir / 'index.html').write_text('<div id="root">AIDM</div>', encoding='utf-8')
    (assets_dir / 'index-contenthash.js').write_text('console.log("aidm")', encoding='utf-8')
    (profile_icons_dir / 'hero.png').write_bytes(b'profile-image')
    (music_dir / 'theme.mp3').write_bytes(b'0123456789')

    app = Flask(__name__)
    configure_frontend_routes(app, dist_dir)
    client = app.test_client()

    root_response = client.get('/')
    spa_response = client.get('/campaigns/10/sessions/20')
    bundle_response = client.get('/assets/index-contenthash.js')
    icon_response = client.get('/profile-icons/hero.png')
    media_response = client.get('/music/theme.mp3', headers={'Range': 'bytes=0-3'})

    assert root_response.headers['Cache-Control'] == 'no-cache, max-age=0, must-revalidate'
    assert spa_response.headers['Cache-Control'] == 'no-cache, max-age=0, must-revalidate'
    assert bundle_response.headers['Cache-Control'] == 'public, max-age=31536000, immutable'
    assert icon_response.headers['Cache-Control'] == (
        'public, max-age=86400, stale-while-revalidate=604800'
    )
    assert media_response.status_code == 206
    assert media_response.data == b'0123'
    assert media_response.headers['Cache-Control'] == (
        'public, max-age=86400, stale-while-revalidate=604800'
    )
