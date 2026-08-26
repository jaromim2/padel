<?php

if (!defined('ABSPATH')) {
	exit;
}

final class Padel_Video_Analysis_REST {
	private static ?self $instance = null;

	public static function instance(): self {
		if (self::$instance === null) {
			self::$instance = new self();
		}

		return self::$instance;
	}

	private function __construct() {
		add_action('rest_api_init', [$this, 'register_routes']);
		add_filter('rest_authentication_errors', [$this, 'allow_private_artifact_requests_without_nonce'], 5);
	}

	public function register_routes(): void {
		register_rest_route('padel-video-analysis/v1', '/upload', [
			'methods' => WP_REST_Server::CREATABLE,
			'callback' => [$this, 'upload'],
			'permission_callback' => [$this, 'can_access'],
		]);

		register_rest_route('padel-video-analysis/v1', '/match-upload', [
			'methods' => WP_REST_Server::CREATABLE,
			'callback' => [$this, 'match_upload'],
			'permission_callback' => [$this, 'can_access'],
		]);

		register_rest_route('padel-video-analysis/v1', '/video/(?P<id>\d+)', [
			'methods' => WP_REST_Server::READABLE,
			'callback' => [$this, 'download_video'],
			'permission_callback' => [$this, 'can_service_access'],
		]);

		register_rest_route('padel-video-analysis/v1', '/private-download/(?P<id>\d+)', [
			'methods' => WP_REST_Server::READABLE,
			'callback' => [$this, 'download_private_video'],
			'permission_callback' => [$this, 'can_private_download_access'],
		]);

		register_rest_route('padel-video-analysis/v1', '/analysis/(?P<id>\d+)', [
			'methods' => WP_REST_Server::READABLE,
			'callback' => [$this, 'get_analysis'],
			'permission_callback' => [$this, 'can_access'],
			'args' => [
				'id' => [
					'validate_callback' => static function ($param) {
						return is_numeric($param) && (int) $param > 0;
					},
				],
			],
		]);

		register_rest_route('padel-video-analysis/v1', '/analysis/(?P<id>\d+)', [
			'methods' => WP_REST_Server::DELETABLE,
			'callback' => [$this, 'delete_analysis'],
			'permission_callback' => [$this, 'can_access'],
		]);

		register_rest_route('padel-video-analysis/v1', '/analysis/(?P<id>\d+)/contact-frame', [
			'methods' => WP_REST_Server::EDITABLE,
			'callback' => [$this, 'update_contact_frame'],
			'permission_callback' => [$this, 'can_access'],
		]);

		register_rest_route('padel-video-analysis/v1', '/analysis/(?P<id>\d+)/selected-player', [
			'methods' => WP_REST_Server::CREATABLE,
			'callback' => [$this, 'select_match_player'],
			'permission_callback' => [$this, 'can_access'],
		]);

		register_rest_route('padel-video-analysis/v1', '/analysis/(?P<id>\d+)/candidate-review', [
			'methods' => WP_REST_Server::CREATABLE,
			'callback' => [$this, 'review_match_candidate'],
			'permission_callback' => [$this, 'can_access'],
		]);

		register_rest_route('padel-video-analysis/v1', '/analysis/(?P<id>\d+)/artifact/(?P<artifact>[a-z0-9_\-]+)', [
			'methods' => WP_REST_Server::READABLE,
			'callback' => [$this, 'download_artifact'],
			'permission_callback' => [$this, 'can_access'],
		]);

		register_rest_route('padel-video-analysis/v1', '/analysis/(?P<id>\d+)/review', [
			'methods' => [WP_REST_Server::READABLE, WP_REST_Server::CREATABLE],
			'callback' => [$this, 'review_analysis'],
			'permission_callback' => [$this, 'can_review_access'],
			'args' => [
				'id' => [
					'validate_callback' => static function ($param) {
						return is_numeric($param) && (int) $param > 0;
					},
				],
			],
		]);

		register_rest_route('padel-video-analysis/v1', '/reviews/calibration-summary', [
			'methods' => WP_REST_Server::READABLE,
			'callback' => [$this, 'calibration_summary'],
			'permission_callback' => [$this, 'can_review_access'],
		]);
	}

	public function can_access(): bool {
		return is_user_logged_in() || $this->restore_browser_session_from_cookie();
	}

	private function restore_browser_session_from_cookie(): bool {
		if (is_user_logged_in()) {
			return true;
		}

		if (!defined('LOGGED_IN_COOKIE') || !isset($_COOKIE[LOGGED_IN_COOKIE])) {
			return false;
		}

		$user_id = wp_validate_auth_cookie((string) $_COOKIE[LOGGED_IN_COOKIE], 'logged_in');
		if (!$user_id) {
			return false;
		}

		wp_set_current_user((int) $user_id);
		return is_user_logged_in();
	}

	public function can_service_access(WP_REST_Request $request): bool {
		$secret = Padel_Video_Analysis_External_Client::instance()->get_service_secret();
		if ($secret === '') {
			return false;
		}

		$provided = (string) $request->get_header('x-padel-api-secret');
		return hash_equals($secret, $provided);
	}

	public function can_review_access(): bool {
		return Padel_Video_Analysis::instance()->can_current_user_review();
	}

	public function can_private_download_access(WP_REST_Request $request): bool|WP_Error {
		$analysis_id = absint($request['id'] ?? 0);
		$post = get_post($analysis_id);
		if (!$post instanceof WP_Post || $post->post_type !== Padel_Video_Analysis::CPT) {
			return new WP_Error('padel_not_found', 'Analysis not found.', ['status' => 404]);
		}

		return Padel_Video_Analysis::instance()->validate_private_download_token(
			$analysis_id,
			(string) $request->get_param('token')
		);
	}

	public function allow_private_artifact_requests_without_nonce(mixed $result): mixed {
		if (!($result instanceof WP_Error)) {
			return $result;
		}

		if ($result->get_error_code() !== 'rest_cookie_invalid_nonce') {
			return $result;
		}

		$request = rest_get_current_request();
		if (!$request instanceof WP_REST_Request) {
			return $result;
		}

		$route = (string) $request->get_route();
		if (!str_starts_with($route, '/padel-video-analysis/v1/analysis/') || !str_contains($route, '/artifact/')) {
			return $result;
		}

		return is_user_logged_in() ? null : $result;
	}

	public function upload(WP_REST_Request $request): WP_REST_Response|WP_Error {
		return $this->handle_upload($request, 'stroke', Padel_Video_Analysis::MAX_FILE_SIZE, Padel_Video_Analysis::MAX_DURATION_SECONDS);
	}

	public function match_upload(WP_REST_Request $request): WP_REST_Response|WP_Error {
		return $this->handle_upload($request, 'match', Padel_Video_Analysis::MAX_MATCH_FILE_SIZE, Padel_Video_Analysis::MAX_MATCH_DURATION_SECONDS);
	}

	private function handle_upload(
		WP_REST_Request $request,
		string $analysis_mode,
		int $max_file_size,
		float $max_duration_seconds
	): WP_REST_Response|WP_Error {
		$user_id = get_current_user_id();
		if (!$user_id) {
			return new WP_Error('padel_unauthorized', 'Login required.', ['status' => 401]);
		}

		$shot_type = sanitize_text_field((string) $request->get_param('shot_type'));
		if ($analysis_mode === 'match' && $shot_type === '') {
			$shot_type = 'forehand';
		}
		$dominant_hand = sanitize_text_field((string) $request->get_param('dominant_hand'));
		$camera_angle = sanitize_text_field((string) $request->get_param('camera_angle'));
		$file = $request->get_file_params()['video_file'] ?? null;

		if (!is_array($file)) {
			return new WP_Error('padel_missing_file', 'Video file is required.', ['status' => 400]);
		}

		$analysis = Padel_Video_Analysis::instance()->create_analysis($user_id, [
			'shot_type' => $shot_type,
			'dominant_hand' => $dominant_hand,
			'camera_angle' => $camera_angle,
		], $analysis_mode);

		if (is_wp_error($analysis)) {
			return $analysis;
		}

		$storage = Padel_Video_Analysis_Storage::instance();
		$file_result = $storage->validate_and_store_upload($file, (int) $analysis->ID, $user_id, $max_file_size, $max_duration_seconds);

		if (is_wp_error($file_result)) {
			wp_delete_post((int) $analysis->ID, true);
			return $file_result;
		}

		Padel_Video_Analysis::instance()->attach_video_to_analysis((int) $analysis->ID, $file_result);
		$fingerprint = Padel_Video_Analysis::instance()->build_submission_fingerprint(
			$user_id,
			$analysis_mode,
			$shot_type,
			$dominant_hand,
			$camera_angle,
			(string) $file_result['sha256']
		);
		Padel_Video_Analysis::instance()->set_meta((int) $analysis->ID, 'submission_fingerprint', $fingerprint);

		$submission = Padel_Video_Analysis::instance()->submit_external_job((int) $analysis->ID);
		if (is_wp_error($submission) && !Padel_Video_Analysis_External_Client::instance()->is_enabled()) {
			Padel_Video_Analysis_Cron::instance()->schedule_analysis((int) $analysis->ID);
		}

		return rest_ensure_response([
			'success' => true,
			'analysis' => Padel_Video_Analysis::instance()->get_record_payload(get_post((int) $analysis->ID)),
		]);
	}

	public function get_analysis(WP_REST_Request $request): WP_REST_Response|WP_Error {
		$user_id = get_current_user_id();
		$analysis_id = absint($request['id'] ?? 0);
		$post = Padel_Video_Analysis::instance()->maybe_get_user_analysis($user_id, $analysis_id);

		if (!$post instanceof WP_Post) {
			return new WP_Error('padel_not_found', 'Analysis not found.', ['status' => 404]);
		}

		return rest_ensure_response([
			'success' => true,
			'analysis' => Padel_Video_Analysis::instance()->get_record_payload($post),
		]);
	}

	public function delete_analysis(WP_REST_Request $request): WP_REST_Response|WP_Error {
		$user_id = get_current_user_id();
		$analysis_id = absint($request['id'] ?? 0);
		$post = get_post($analysis_id);

		if (!$post instanceof WP_Post || $post->post_type !== Padel_Video_Analysis::CPT) {
			return new WP_Error('padel_not_found', 'Analysis not found.', ['status' => 404]);
		}

		if ((int) $post->post_author !== $user_id) {
			return new WP_Error('padel_forbidden', 'You can only delete your own analyses.', ['status' => 403]);
		}

		$deleted = Padel_Video_Analysis::instance()->delete_analysis($analysis_id);
		if (!$deleted) {
			return new WP_Error('padel_delete_failed', 'Unable to delete the analysis.', ['status' => 500]);
		}

		return rest_ensure_response([
			'success' => true,
		]);
	}

	public function update_contact_frame(WP_REST_Request $request): WP_REST_Response|WP_Error {
		$user_id = get_current_user_id();
		$analysis_id = absint($request['id'] ?? 0);
		$frame_index = absint($request->get_param('frame_index'));
		$post = get_post($analysis_id);

		if (!$post instanceof WP_Post || $post->post_type !== Padel_Video_Analysis::CPT) {
			return new WP_Error('padel_not_found', 'Analysis not found.', ['status' => 404]);
		}

		if ((int) $post->post_author !== $user_id) {
			return new WP_Error('padel_forbidden', 'You can only update your own analyses.', ['status' => 403]);
		}

		if ($frame_index < 0) {
			return new WP_Error('padel_invalid_frame', 'Frame index must be positive.', ['status' => 400]);
		}

		$external_job_id = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'external_job_id', '');
		if ($external_job_id === '') {
			return new WP_Error('padel_external_job_missing', 'External job is not available.', ['status' => 409]);
		}

		$response = Padel_Video_Analysis_External_Client::instance()->update_contact_frame($external_job_id, $frame_index);
		if (is_wp_error($response)) {
			return $response;
		}

		Padel_Video_Analysis::instance()->apply_external_job_response($analysis_id, $response, $external_job_id);
		Padel_Video_Analysis::instance()->set_meta($analysis_id, 'manual_contact_frame_index', $frame_index);
		Padel_Video_Analysis::instance()->set_meta($analysis_id, 'manual_contact_frame_source', 'manual');
		Padel_Video_Analysis::instance()->set_meta($analysis_id, 'manual_contact_frame_updated_at', gmdate('c'));

		return rest_ensure_response([
			'success' => true,
			'analysis' => Padel_Video_Analysis::instance()->get_record_payload(get_post($analysis_id)),
		]);
	}

	public function download_artifact(WP_REST_Request $request): WP_REST_Response|WP_Error {
		$user_id = get_current_user_id();
		$analysis_id = absint($request['id'] ?? 0);
		$artifact = sanitize_key((string) $request->get_param('artifact'));
		$post = Padel_Video_Analysis::instance()->maybe_get_user_analysis($user_id, $analysis_id);

		if (!$post instanceof WP_Post) {
			return new WP_Error('padel_not_found', 'Analysis not found.', ['status' => 404]);
		}

		$reference = $this->resolve_artifact_reference($artifact, $analysis_id);
		if (!is_array($reference)) {
			return new WP_Error('padel_artifact_not_available', 'Artifact not available.', ['status' => 404]);
		}

		$mime_type = (string) ($reference['mime_type'] ?? 'application/octet-stream');
		$filename = (string) ($reference['filename'] ?? ($artifact . '.bin'));
		$extension = pathinfo($filename, PATHINFO_EXTENSION);
		if ($extension === '') {
			$extension = str_contains($mime_type, 'json') ? 'json' : (str_starts_with($mime_type, 'image/') ? 'jpg' : (str_starts_with($mime_type, 'video/') ? 'mp4' : 'bin'));
		}

		$storage = Padel_Video_Analysis_Storage::instance();
		$cache_path = $storage->get_artifact_cache_path($analysis_id, $artifact, $extension);
		if ($this->is_cached_artifact_valid($cache_path, $mime_type)) {
			return $this->stream_artifact_file($cache_path, $mime_type, $filename);
		}

		if (file_exists($cache_path)) {
			@unlink($cache_path);
		}

		if (!file_exists($cache_path) || filesize($cache_path) <= 0) {
			$external_job_id = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'external_job_id', '');
			if ($external_job_id === '') {
				$generated_path = $this->generate_local_artifact($analysis_id, $artifact, $mime_type, $filename, $cache_path);
				if (is_string($generated_path)) {
					return $this->stream_artifact_file($generated_path, $mime_type, $filename);
				}

				return new WP_Error('padel_artifact_missing', 'Artifact is not available.', ['status' => 404]);
			}

			$response = Padel_Video_Analysis_External_Client::instance()->fetch_artifact((int) $external_job_id, $artifact);
			if (is_wp_error($response)) {
				$generated_path = $this->generate_local_artifact($analysis_id, $artifact, $mime_type, $filename, $cache_path);
				if (is_string($generated_path)) {
					return $this->stream_artifact_file($generated_path, $mime_type, $filename);
				}

				return $response;
			}

			$status_code = (int) ($response['status_code'] ?? 0);
			$body = (string) ($response['body'] ?? '');
			if ($status_code < 200 || $status_code >= 300 || !$this->artifact_body_matches_mime($body, $mime_type)) {
				$generated_path = $this->generate_local_artifact($analysis_id, $artifact, $mime_type, $filename, $cache_path);
				if (is_string($generated_path)) {
					return $this->stream_artifact_file($generated_path, $mime_type, $filename);
				}

				return new WP_Error('padel_artifact_missing', 'Artifact is not available.', ['status' => 404]);
			}

			$cache_dir = dirname($cache_path);
			if (!is_dir($cache_dir)) {
				wp_mkdir_p($cache_dir);
			}
			file_put_contents($cache_path, $body);
			@chmod($cache_path, 0600);

			if ($this->is_cached_artifact_valid($cache_path, $mime_type)) {
				return $this->stream_artifact_file($cache_path, $mime_type, $filename);
			}

			@unlink($cache_path);
			$generated_path = $this->generate_local_artifact($analysis_id, $artifact, $mime_type, $filename, $cache_path);
			if (is_string($generated_path)) {
				return $this->stream_artifact_file($generated_path, $mime_type, $filename);
			}

			return new WP_Error('padel_artifact_missing', 'Artifact is not available.', ['status' => 404]);
		}

		if (str_starts_with($mime_type, 'video/')) {
			return $this->stream_file_with_range($cache_path, $mime_type, $filename);
		}

		return $this->stream_artifact_file($cache_path, $mime_type, $filename);
	}

	public function select_match_player(WP_REST_Request $request): WP_REST_Response|WP_Error {
		$user_id = get_current_user_id();
		$analysis_id = absint($request['id'] ?? 0);
		$selected_candidate_id = sanitize_text_field((string) $request->get_param('selected_player_candidate_id'));
		$post = Padel_Video_Analysis::instance()->maybe_get_user_analysis($user_id, $analysis_id, 'match');

		if (!$post instanceof WP_Post) {
			return new WP_Error('padel_not_found', 'Analysis not found.', ['status' => 404]);
		}
		if ($selected_candidate_id === '') {
			return new WP_Error('padel_invalid_selection', 'A player selection is required.', ['status' => 400]);
		}

		$response = Padel_Video_Analysis::instance()->select_match_player($analysis_id, $selected_candidate_id);
		if (is_wp_error($response)) {
			return $response;
		}

		return rest_ensure_response([
			'success' => true,
			'analysis' => Padel_Video_Analysis::instance()->get_record_payload(get_post($analysis_id)),
		]);
	}

	public function review_analysis(WP_REST_Request $request): WP_REST_Response|WP_Error {
		$analysis_id = absint($request['id'] ?? 0);
		$post = Padel_Video_Analysis::instance()->maybe_get_user_analysis(get_current_user_id(), $analysis_id);
		if (!$post instanceof WP_Post) {
			$post = get_post($analysis_id);
		}

		if (!$post instanceof WP_Post || $post->post_type !== Padel_Video_Analysis::CPT) {
			return new WP_Error('padel_not_found', 'Analysis not found.', ['status' => 404]);
		}

		if ($request->get_method() === 'GET') {
			return rest_ensure_response([
				'success' => true,
				'analysis' => Padel_Video_Analysis::instance()->get_record_payload($post),
			]);
		}

		$payload = $request->get_json_params();
		if (!is_array($payload)) {
			$payload = $request->get_params();
		}

		$rules_version = sanitize_text_field((string) ($request->get_param('rules_version') ?? ($payload['rules_version'] ?? '')));
		$response = Padel_Video_Analysis::instance()->save_review($analysis_id, $payload, $rules_version !== '' ? $rules_version : null);
		if (is_wp_error($response)) {
			return $response;
		}

		return rest_ensure_response([
			'success' => true,
			'analysis' => Padel_Video_Analysis::instance()->get_record_payload(get_post($analysis_id)),
			'review' => $response,
		]);
	}

	public function calibration_summary(WP_REST_Request $request): WP_REST_Response|WP_Error {
		$rules_version = sanitize_text_field((string) $request->get_param('rules_version'));
		return rest_ensure_response([
			'success' => true,
			'summary' => Padel_Video_Analysis::instance()->get_review_calibration_summary($rules_version !== '' ? $rules_version : null),
		]);
	}

	public function review_match_candidate(WP_REST_Request $request): WP_REST_Response|WP_Error {
		$user_id = get_current_user_id();
		$analysis_id = absint($request['id'] ?? 0);
		$post = Padel_Video_Analysis::instance()->maybe_get_user_analysis($user_id, $analysis_id, 'match');
		if (!$post instanceof WP_Post) {
			return new WP_Error('padel_not_found', 'Analysis not found.', ['status' => 404]);
		}

		$candidate_id = sanitize_text_field((string) $request->get_param('candidate_id'));
		$review_label = sanitize_key((string) $request->get_param('review_label'));
		$notes = sanitize_textarea_field((string) $request->get_param('notes'));
		$allowed = ['real', 'false', 'missed', 'unclear'];
		if ($candidate_id === '' || !in_array($review_label, $allowed, true)) {
			return new WP_Error('padel_invalid_review', 'Invalid review payload.', ['status' => 400]);
		}

		$reviews = Padel_Video_Analysis::instance()->get_meta($analysis_id, 'manual_match_reviews', []);
		if (!is_array($reviews)) {
			$reviews = [];
		}

		$reviews[$candidate_id] = [
			'candidate_id' => $candidate_id,
			'review_label' => $review_label,
			'notes' => $notes,
			'updated_at' => gmdate('c'),
			'user_id' => $user_id,
		];
		Padel_Video_Analysis::instance()->set_meta($analysis_id, 'manual_match_reviews', $reviews);

		return rest_ensure_response([
			'success' => true,
			'analysis' => Padel_Video_Analysis::instance()->get_record_payload(get_post($analysis_id)),
		]);
	}

	private function resolve_artifact_reference(string $artifact, int $analysis_id): array|false {
		$post = get_post($analysis_id);
		if (!$post instanceof WP_Post || $post->post_type !== Padel_Video_Analysis::CPT) {
			return false;
		}

		$result = Padel_Video_Analysis::instance()->get_meta($analysis_id, 'analysis_result', []);
		if (!is_array($result)) {
			$result = [];
		}

		if ($artifact === 'annotated-video') {
			return $this->maybe_normalize_reference($result['artifacts']['annotated_video'] ?? null);
		}

		if ($artifact === 'match-preview') {
			return $this->maybe_normalize_reference($result['match']['artifacts']['preview_image'] ?? null);
		}

		if ($artifact === 'tracking-preview') {
			return $this->maybe_normalize_reference($result['match']['artifacts']['tracking_preview_image'] ?? null);
		}

		if ($artifact === 'metadata') {
			return $this->maybe_normalize_reference($result['match']['artifacts']['metadata'] ?? null);
		}

		if (preg_match('/^preview-frame-(\d+)$/', $artifact, $matches)) {
			$requested_frame = (int) $matches[1];
			$frames = $result['match']['preview']['frames'] ?? [];
			if (is_array($frames)) {
				foreach ($frames as $frame) {
					if (!is_array($frame) || (int) ($frame['frame_index'] ?? -1) !== $requested_frame) {
						continue;
					}
					return $this->maybe_normalize_reference($frame['artifact'] ?? null);
				}
			}
		}

		if (preg_match('/^(candidate-[a-zA-Z0-9_-]+)-(clip|thumbnail)$/', $artifact, $matches)) {
			$candidate_id = $matches[1];
			$kind = $matches[2];
			$candidates = $result['match']['stroke_candidates'] ?? [];
			if (is_array($candidates)) {
				foreach ($candidates as $candidate) {
					if (!is_array($candidate) || (string) ($candidate['candidate_id'] ?? '') !== $candidate_id) {
						continue;
					}
					$artifact_key = $kind === 'clip' ? 'clip' : 'thumbnail';
					return $this->maybe_normalize_reference($candidate['artifacts'][$artifact_key] ?? null);
				}
			}
		}

		return false;
	}

	private function maybe_normalize_reference(mixed $reference): array|false {
		if (!is_array($reference)) {
			return false;
		}

		$storage_key = (string) ($reference['storage_key'] ?? '');
		if ($storage_key === '') {
			return false;
		}

		return $reference;
	}

	private function is_cached_artifact_valid(string $cache_path, string $mime_type): bool {
		if (!file_exists($cache_path) || filesize($cache_path) <= 0) {
			return false;
		}

		$size = filesize($cache_path);
		if (str_starts_with($mime_type, 'image/')) {
			if ($size < 1024) {
				return false;
			}
		} elseif (str_starts_with($mime_type, 'video/')) {
			if ($size < 10240) {
				return false;
			}
		} elseif (str_contains($mime_type, 'json')) {
			if ($size < 2) {
				return false;
			}
		}

		$finfo = finfo_open(FILEINFO_MIME_TYPE);
		if (!$finfo) {
			return true;
		}

		$detected = (string) finfo_file($finfo, $cache_path);
		if (is_resource($finfo)) {
			finfo_close($finfo);
		}

		return $detected !== '' && str_starts_with($detected, strtok($mime_type, '/'));
	}

	private function artifact_body_matches_mime(string $body, string $mime_type): bool {
		if ($body === '') {
			return false;
		}

		$length = strlen($body);
		if (str_starts_with($mime_type, 'image/') && $length < 1024) {
			return false;
		}
		if (str_starts_with($mime_type, 'video/') && $length < 10240) {
			return false;
		}
		if (str_contains($mime_type, 'json') && $length < 2) {
			return false;
		}

		$finfo = finfo_open(FILEINFO_MIME_TYPE);
		if (!$finfo) {
			return true;
		}

		$detected = (string) finfo_buffer($finfo, $body);
		if (is_resource($finfo)) {
			finfo_close($finfo);
		}

		return $detected !== '' && str_starts_with($detected, strtok($mime_type, '/'));
	}

	private function stream_artifact_file(string $path, string $mime_type, string $filename): WP_REST_Response {
		nocache_headers();
		return new WP_REST_Response(
			file_get_contents($path) ?: '',
			200,
			[
				'Content-Type' => $mime_type,
				'Content-Disposition' => 'inline; filename="' . $filename . '"',
				'X-Content-Type-Options' => 'nosniff',
			]
		);
	}

	private function generate_local_artifact(int $analysis_id, string $artifact, string $mime_type, string $filename, string $cache_path): string|WP_Error {
		if (!str_starts_with($mime_type, 'image/')) {
			return new WP_Error('padel_artifact_missing', 'Artifact is not available.', ['status' => 404]);
		}

		$video_path = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_path', '');
		if ($video_path === '' || !file_exists($video_path)) {
			return new WP_Error('padel_missing_video', 'Video file is missing.', ['status' => 404]);
		}

		$timestamp_ms = $this->resolve_artifact_timestamp_ms($analysis_id, $artifact);
		if ($timestamp_ms === null) {
			return new WP_Error('padel_artifact_missing', 'Artifact is not available.', ['status' => 404]);
		}

		$ffmpeg = trim((string) shell_exec('command -v ffmpeg 2>/dev/null'));
		if ($ffmpeg === '') {
			return new WP_Error('padel_ffmpeg_missing', 'Video extraction tool is not available on this server.', ['status' => 500]);
		}

		$cache_dir = dirname($cache_path);
		if (!is_dir($cache_dir)) {
			wp_mkdir_p($cache_dir);
		}

		$seek = max(0.0, ((float) $timestamp_ms) / 1000.0);
		$command = sprintf(
			'%s -y -ss %s -i %s -frames:v 1 -q:v 2 %s 2>/dev/null',
			escapeshellcmd($ffmpeg),
			escapeshellarg((string) $seek),
			escapeshellarg($video_path),
			escapeshellarg($cache_path)
		);

		$output = [];
		$return_code = 0;
		@exec($command, $output, $return_code);
		if ($return_code !== 0 || !file_exists($cache_path) || filesize($cache_path) <= 0) {
			return new WP_Error('padel_artifact_missing', 'Artifact is not available.', ['status' => 404]);
		}

		@chmod($cache_path, 0600);
		return $cache_path;
	}

	private function resolve_artifact_timestamp_ms(int $analysis_id, string $artifact): ?int {
		$result = Padel_Video_Analysis::instance()->get_meta($analysis_id, 'analysis_result', []);
		if (!is_array($result)) {
			$result = [];
		}

		if ($artifact === 'match-preview') {
			$preview = $result['match']['preview'] ?? [];
			if (is_array($preview) && isset($preview['timestamp_ms']) && is_numeric($preview['timestamp_ms'])) {
				return (int) $preview['timestamp_ms'];
			}
			return null;
		}

		if (preg_match('/^preview-frame-(\d+)$/', $artifact, $matches)) {
			$requested_frame = (int) $matches[1];
			$frames = $result['match']['preview']['frames'] ?? [];
			if (is_array($frames)) {
				foreach ($frames as $frame) {
					if (!is_array($frame) || (int) ($frame['frame_index'] ?? -1) !== $requested_frame) {
						continue;
					}
					if (isset($frame['timestamp_ms']) && is_numeric($frame['timestamp_ms'])) {
						return (int) $frame['timestamp_ms'];
					}
					break;
				}
			}
			return null;
		}

		if (preg_match('/^(candidate-[a-zA-Z0-9_-]+)-thumbnail$/', $artifact, $matches)) {
			$candidate_id = $matches[1];
			$candidates = $result['match']['stroke_candidates'] ?? [];
			if (is_array($candidates)) {
				foreach ($candidates as $candidate) {
					if (!is_array($candidate) || (string) ($candidate['candidate_id'] ?? '') !== $candidate_id) {
						continue;
					}
					foreach (['peak_timestamp_ms', 'start_timestamp_ms', 'end_timestamp_ms'] as $key) {
						if (isset($candidate[$key]) && is_numeric($candidate[$key])) {
							return (int) $candidate[$key];
						}
					}
				}
			}
			return null;
		}

		return null;
	}

	public function download_video(WP_REST_Request $request) {
		$analysis_id = absint($request['id'] ?? 0);
		$post = get_post($analysis_id);

		if (!$post instanceof WP_Post || $post->post_type !== Padel_Video_Analysis::CPT) {
			return new WP_Error('padel_not_found', 'Analysis not found.', ['status' => 404]);
		}

		$path = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_path', '');
		if ($path === '' || !file_exists($path)) {
			return new WP_Error('padel_video_missing', 'Video not found.', ['status' => 404]);
		}

		nocache_headers();
		$status = 200;
		$mime_type = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_mime_type', 'video/mp4');
		$size = (int) filesize($path);

		if (!headers_sent()) {
			status_header($status);
			header('Content-Type: ' . ($mime_type !== '' ? $mime_type : 'application/octet-stream'));
			header('Content-Length: ' . $size);
			header('Content-Disposition: inline; filename="' . basename($path) . '"');
			header('X-Content-Type-Options: nosniff');
		}

		readfile($path);
		exit;
	}

	public function download_private_video(WP_REST_Request $request) {
		$analysis_id = absint($request['id'] ?? 0);
		$post = get_post($analysis_id);

		if (!$post instanceof WP_Post || $post->post_type !== Padel_Video_Analysis::CPT) {
			return new WP_Error('padel_not_found', 'Analysis not found.', ['status' => 404]);
		}

		$validation = Padel_Video_Analysis::instance()->validate_private_download_token(
			$analysis_id,
			(string) $request->get_param('token')
		);
		if (is_wp_error($validation)) {
			return $validation;
		}

		$path = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_path', '');
		if ($path === '' || !file_exists($path)) {
			return new WP_Error('padel_video_missing', 'Video not found.', ['status' => 404]);
		}

		Padel_Video_Analysis::instance()->mark_private_download_token_used($analysis_id);

		nocache_headers();
		$status = 200;
		$mime_type = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_mime_type', 'video/mp4');
		$size = (int) filesize($path);

		if (!headers_sent()) {
			status_header($status);
			header('Content-Type: ' . ($mime_type !== '' ? $mime_type : 'application/octet-stream'));
			header('Content-Length: ' . $size);
			header('Content-Disposition: inline; filename="' . basename($path) . '"');
			header('X-Content-Type-Options: nosniff');
		}

		readfile($path);
		exit;
	}

	private function stream_file_with_range(string $path, string $mime_type, string $filename): void {
		nocache_headers();
		$size = (int) filesize($path);
		$range = $_SERVER['HTTP_RANGE'] ?? '';
		$start = 0;
		$end = max(0, $size - 1);
		$status = 200;

		if (is_string($range) && preg_match('/bytes=(\d+)-(\d*)/', $range, $matches)) {
			$start = max(0, (int) $matches[1]);
			$end = $matches[2] !== '' ? min($end, (int) $matches[2]) : $end;
			if ($start <= $end && $start < $size) {
				$status = 206;
			} else {
				$start = 0;
				$end = max(0, $size - 1);
			}
		}

		if (!headers_sent()) {
			status_header($status);
			header('Content-Type: ' . $mime_type);
			header('Accept-Ranges: bytes');
			header('Content-Disposition: inline; filename="' . $filename . '"');
			header('X-Content-Type-Options: nosniff');
			header('Content-Length: ' . ((int) $end - (int) $start + 1));
			if ($status === 206) {
				header(sprintf('Content-Range: bytes %d-%d/%d', $start, $end, $size));
			}
		}

		$handle = fopen($path, 'rb');
		if ($handle === false) {
			return;
		}
		fseek($handle, $start);
		$remaining = $end - $start + 1;
		while ($remaining > 0 && !feof($handle)) {
			$chunk = fread($handle, min(8192, $remaining));
			if ($chunk === false || $chunk === '') {
				break;
			}
			echo $chunk;
			$remaining -= strlen($chunk);
			@flush();
		}
		fclose($handle);
		exit;
	}
}
