# Padel Video Analysis MVP - Phase 0 Plan

## Scope

This plan assumes `/var/www/padel` is the new plugin repository. The fresh WordPress site for this MVP is now installed in `/var/www/padel/site` and served as `http://padel.local` with its own database.

Implementation note:

- The initial repository inspection below documents the older `sport` install as historical context for how the existing product was structured.
- The actual Phase 1 MVP is isolated in the new `padel` site and does not depend on the `sport` site.

Phase 0 only: inspect, map, and plan. No production code changes yet.

## Repository Findings

The WordPress site in `/var/www/sport` is a custom PHP build with a heavily customized theme and bespoke auth/API behavior.

- The site root is configured in [wp-config.php](/var/www/sport/wp-config.php) and points to `WP_HOME` / `WP_SITEURL` at `https://roshax.club`.
- The main site behavior lives in the child theme at [wp-content/themes/proball-he/functions.php](/var/www/sport/wp-content/themes/proball-he/functions.php), which registers custom post types, REST routes, user-meta state, and PWA assets.
- Authentication is customized in [wp-content/themes/proball-he/custom-login.php](/var/www/sport/wp-content/themes/proball-he/custom-login.php) and reinforced by [wp-content/mu-plugins/proball-login-fix.php](/var/www/sport/wp-content/mu-plugins/proball-login-fix.php).
- Admin-facing structured content already uses a plugin pattern in [wp-content/plugins/proball-crm-admin/proball-crm-admin.php](/var/www/sport/wp-content/plugins/proball-crm-admin/proball-crm-admin.php).
- Deployment is handled by a shell script, [deploy.sh](/var/www/sport/deploy.sh), not by Docker or a CI workflow in the repo root.
- The current site stores structured data in WordPress posts, post meta, user meta, options, and transients rather than custom tables.
- There is a separate Expo/React Native app under [mobile/proball-app/package.json](/var/www/sport/mobile/proball-app/package.json), but that is not the WordPress site surface.

## Existing Architecture

The site is a custom WordPress application built around:

- Astra parent theme plus the `proball-he` child theme.
- Custom post types for programs, nutrition, and coaching in [functions.php](/var/www/sport/wp-content/themes/proball-he/functions.php).
- Custom REST routes under the `proball-he/v1` namespace in [functions.php](/var/www/sport/wp-content/themes/proball-he/functions.php).
- Logged-in user state stored in user meta and transient-backed bearer tokens in [functions.php](/var/www/sport/wp-content/themes/proball-he/functions.php).
- A custom login flow and login-page routing in [custom-login.php](/var/www/sport/wp-content/themes/proball-he/custom-login.php).
- Site-wide cookie and mail adjustments in [functions.php](/var/www/sport/wp-content/themes/proball-he/functions.php) and [proball-login-fix.php](/var/www/sport/wp-content/mu-plugins/proball-login-fix.php).

## Frontend Framework And Build System

For the WordPress site itself, there is no app-style frontend framework or repository-level JS build pipeline in `/var/www/sport`.

- The site is server-rendered PHP with theme CSS and inline JS.
- The child theme enqueues CSS directly in [functions.php](/var/www/sport/wp-content/themes/proball-he/functions.php).
- The theme includes a `react-source` asset folder, but there is no root-level `package.json`, `vite.config`, `webpack.config`, or `docker-compose` in the WordPress site root.
- The only visible modern build system in this workspace is the separate Expo app in [mobile/proball-app/package.json](/var/www/sport/mobile/proball-app/package.json), which is unrelated to the WordPress MVP.

## Authentication Plan

Current auth on the WordPress site works like this:

- Browser login is redirected through `/login` and `/forgot-password` in [custom-login.php](/var/www/sport/wp-content/themes/proball-he/custom-login.php).
- The theme also exposes a REST login endpoint at `proball/v1/login` in [custom-login.php](/var/www/sport/wp-content/themes/proball-he/custom-login.php).
- Mobile/API auth uses bearer tokens issued into transients in [functions.php](/var/www/sport/wp-content/themes/proball-he/functions.php).
- Logged-in browser requests already rely on WordPress auth cookies, with SameSite cookie handling adjusted in [functions.php](/var/www/sport/wp-content/themes/proball-he/functions.php) and [proball-login-fix.php](/var/www/sport/wp-content/mu-plugins/proball-login-fix.php).

Recommended plugin auth for Phase 1:

- Use native WordPress login cookies for the upload page.
- Require a REST nonce for browser requests.
- Require `is_user_logged_in()` and ownership checks for read/delete actions.
- Do not introduce a second authentication system for the MVP.
- Reserve a future service token or application-password flow for the external Python analyzer, but do not depend on it in Phase 1.

## Data, Storage, And API Conventions

Observed conventions in the site:

- Custom business objects are commonly stored as custom post types plus post meta, as seen in [functions.php](/var/www/sport/wp-content/themes/proball-he/functions.php) and [proball-crm-admin.php](/var/www/sport/wp-content/plugins/proball-crm-admin/proball-crm-admin.php).
- User-specific state is stored in user meta with `proball_*` keys in [functions.php](/var/www/sport/wp-content/themes/proball-he/functions.php).
- Ephemeral workflow state is stored in transients in [functions.php](/var/www/sport/wp-content/themes/proball-he/functions.php).
- Existing REST routes return JSON payloads from WordPress callbacks, usually with `permission_callback => '__return_true'` and then internal auth checks in the callback.
- File uploads in the site already land under `wp-content/uploads`, including PDF and image artifacts, but there is no existing private-video storage convention to reuse.

Recommended MVP conventions:

- Use a dedicated custom post type for video-analysis records.
- Store the analysis status and form selections in post meta.
- Store the private video path in post meta, not a public attachment URL.
- Keep the object model flat and JSON-friendly so a future Python worker can read and write it without changing the UI.

## Backend And WordPress Integration Points

The new plugin should integrate through WordPress, not through the theme.

Best fit:

- Register a new plugin under `/var/www/padel` for development.
- Install it later into the live site at `wp-content/plugins/padel-video-analysis`.
- Create a page called `ניתוח חבטה` on plugin activation, using a shortcode or block rendered by the plugin.
- Expose REST routes from the plugin for upload, status polling, and delete.
- Leave the existing theme and existing custom post types untouched.

This is the smallest reversible path because it avoids editing the current child theme and keeps the MVP isolated.

## Recommended Architecture

Recommended: a separate plugin repository rooted in `/var/www/padel`, packaged as `padel-video-analysis`.

Why this is the best fit:

- It is isolated from the existing product theme and login code.
- It can be installed or removed without touching the live theme.
- It matches the user's request for a new project in the `padel` folder.
- It keeps the future Python analyzer boundary clean.

MVP shape:

- WordPress plugin handles page rendering, validation, record creation, status transitions, and deletion.
- Video files are stored privately under a dedicated uploads subdirectory.
- A mock processor moves records `uploaded -> processing -> completed` without real video analysis.
- Future Python integration will be added behind the same record and REST surface.

## Alternatives Considered

1. Modify the existing child theme.
- Rejected because it couples the MVP to presentation code and makes later extraction harder.

2. Put the feature into the existing `proball-he` theme functions.
- Rejected because the theme already owns many unrelated concerns and the plugin would be hard to disentangle later.

3. Add a new directory inside an existing monorepo.
- Rejected because the current target workspace is a new `padel` folder, not an active monorepo checkout.

4. Use a custom database table immediately.
- Rejected for Phase 1 because the site already relies on post meta/user meta conventions and a custom table adds migration and maintenance cost.

## Phase 1 File Plan

Create these files in the new plugin repository:

- `padel-video-analysis.php`
- `includes/class-padel-video-analysis.php`
- `includes/class-padel-video-analysis-rest.php`
- `includes/class-padel-video-analysis-shortcode.php`
- `includes/class-padel-video-analysis-storage.php`
- `includes/class-padel-video-analysis-cron.php`
- `assets/css/frontend.css`
- `assets/js/frontend.js`
- `uninstall.php`
- `readme.txt`

Optional if the UI is cleaner as a dedicated template:

- `templates/analysis-page.php`

No files in `/var/www/sport` should need modification for Phase 1 if we keep the integration plugin-only.

## API Integration Plan

Phase 1 REST endpoints should be plugin-owned and versioned separately, for example:

- `POST /wp-json/padel-video-analysis/v1/upload`
- `GET /wp-json/padel-video-analysis/v1/analysis/{id}`
- `DELETE /wp-json/padel-video-analysis/v1/analysis/{id}`

Recommended request/response shape:

- Upload accepts `shot_type`, `dominant_hand`, `camera_angle`, and `video_file`.
- Upload returns `analysis_id`, `status`, and display-safe metadata.
- Status endpoint returns the same shape the UI needs to render progress.
- Delete removes both the record and the private file.

Future Python compatibility:

- Keep `analysis_id` stable and opaque.
- Store a `processor` field with a default of `local_mock`.
- Reserve `external_job_id`, `processing_started_at`, and `completed_at` meta keys even in the mock implementation.
- Make the final response schema identical whether the result came from the local mock or a future Python worker.
- Add a service-side job status callback for the analyzer boundary, such as `POST /api/v1/jobs/{job_id}/status`, so a future Python worker can mark a job `processing`, `completed`, or `failed` without changing the WordPress UI contract.
- Provide a thin worker CLI, such as `python -m app.worker --job-id <job_id>`, that reads the existing job contract and drives the callback endpoint during local development.

## Authentication Plan

The plugin should use the WordPress logged-in session and REST nonce flow.

Planned checks:

- `is_user_logged_in()` for all front-end actions.
- `current_user_can('read')` or a tighter plugin-specific capability for REST routes.
- REST nonce verification on upload and delete requests.
- Ownership verification so only the creator can read or delete their analysis.

No custom password system, token exchange, or public anonymous upload flow in Phase 1.

## Storage Plan

Store each analysis as one private WordPress record with a private file on disk.

Recommended storage model:

- Custom post type stores ownership, status, and metadata.
- Post meta stores shot type, dominant hand, camera angle, duration, file path, mime type, size, and result payload.
- Private video files live under a dedicated uploads subdirectory such as `wp-content/uploads/padel-video-analysis/`.
- Protect that directory with `index.php` and server deny rules.
- Do not return a public file URL from the API.

Validation rules:

- Accept only `mp4`, `mov`, and `webm`.
- Validate the actual uploaded file type, not only the extension.
- Enforce the 15-second and 75MB limits during upload handling.
- Reject damaged or mislabelled files.

## Local Development Plan

Recommended local workflow:

- Develop the plugin in `/var/www/padel`.
- Install or symlink the plugin into the live site only after review.
- Use the existing WordPress instance at `/var/www/sport` for browser smoke tests.
- Keep the plugin self-contained so the live theme is unchanged.

If later we add a build step:

- Only add it for the plugin assets, not for the entire WordPress site.
- Keep the UI bundle small and reloadable.

For the Python service, local development should support two compose targets:

- `padel-analysis-service` for the FastAPI API
- `padel-analysis-worker` for the stub worker via `docker compose up -d padel-analysis-worker` or `docker compose run --rm padel-analysis-worker --job-id <job_id>`

The worker itself should keep its polling loop separate from the processing strategy, with a dedicated processor object that can later be swapped for a real analyzer adapter.

## Testing Plan

Proposed validation commands once Phase 1 exists:

- `find . -name '*.php' -not -path './vendor/*' -print0 | xargs -0 -n1 php -l`
- `php -l padel-video-analysis.php`
- `wp plugin activate padel-video-analysis`
- `wp option get permalink_structure`
- `wp post list --post_type=padel_video_analysis --fields=ID,post_title,post_status`
- `docker compose run --rm padel-analysis-worker --help`
- `docker compose run --rm padel-analysis-worker --poll`
- `curl` or browser-based REST tests against the upload and status endpoints with a logged-in session and nonce
- Browser smoke test for upload, polling, completion, and delete

We should not claim success until:

- A real video upload has been executed.
- The record status has advanced through the mock flow.
- Delete has been exercised end to end.

## Risks And Unclear Assumptions

- `/var/www/padel` is empty right now, so the plugin will be a new repository rather than a refactor of existing code.
- The live WordPress site has no current private-video storage convention, so the storage directory and deny rules must be designed carefully.
- Front-end uploads from logged-in subscribers may not have `upload_files`, so the plugin should not rely on that capability alone.
- Server limits such as PHP `upload_max_filesize`, `post_max_size`, and timeout values may block 75MB uploads unless verified.
- Video duration validation will likely require reading metadata, so we need to confirm whether PHP has a reliable probe available on this host or whether a later worker should own that check.
- The future Python service boundary is not yet defined, so the plugin should keep the data model and API shape stable and add integration points without hard-coding the transport.
- There is no repo-level CI or Docker convention in `/var/www/sport`, so the plugin should be designed to run without assuming either one.

## Phase 1 Acceptance Criteria

Phase 1 is complete only when all of these are true:

- The `padel-video-analysis` plugin exists in the new `padel` repo.
- A logged-in user can open a `ניתוח חבטה` page created by the plugin.
- The UI allows choosing `Forehand`, dominant hand, and camera angle.
- The UI uploads a valid video file and rejects invalid or oversized files.
- The uploaded file is stored privately and no direct public URL is exposed.
- A WordPress analysis record is created and linked to the current user.
- Status transitions from `uploaded` to `processing` to `completed` happen in the mock flow.
- REST endpoints exist for upload, status, and delete.
- Delete removes both the record and the private file.
- The response and stored data are ready for a later external Python processor without changing the front-end contract.
