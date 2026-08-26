<?php

if (!defined('ABSPATH')) {
	exit;
}

final class Padel_Video_Analysis {
	public const CPT = 'padel_video_analysis';
	public const PAGE_SLUG = 'padel-shot-analysis';
	public const PAGE_TITLE = 'ניתוח חבטה';
	public const PAGE_OPTION = 'padel_video_analysis_page_id';
	public const REVIEW_CAPABILITY = 'padel_analysis_review';
	public const STATUS_UPLOADED = 'uploaded';
	public const STATUS_PROCESSING = 'processing';
	public const STATUS_AWAITING_PLAYER_SELECTION = 'awaiting_player_selection';
	public const STATUS_COMPLETED = 'completed';
	public const STATUS_FAILED = 'failed';
	public const PROCESSOR_LOCAL = 'local_mock';
	public const MAX_FILE_SIZE = 78643200; // 75MB
	public const MAX_DURATION_SECONDS = 35;
	public const MAX_MATCH_FILE_SIZE = 1073741824; // 1GB
	public const MAX_MATCH_DURATION_SECONDS = 14400; // 4 hours
	public const DOWNLOAD_TOKEN_TTL_SECONDS = 300;

	private static ?self $instance = null;

	public static function instance(): self {
		if (self::$instance === null) {
			self::$instance = new self();
		}

		return self::$instance;
	}

	public static function activate(): void {
		$instance = self::instance();
		$instance->register_post_type();
		$instance->grant_review_capability();
		Padel_Video_Analysis_Review_Store::instance()->activate();
		$instance->maybe_create_page(true);
		if (class_exists('Padel_Match_Analysis')) {
			Padel_Match_Analysis::activate();
		}
		flush_rewrite_rules();
	}

	public static function deactivate(): void {
		Padel_Video_Analysis_Cron::unschedule_all();
		if (class_exists('Padel_Match_Analysis')) {
			Padel_Match_Analysis::deactivate();
		}
		flush_rewrite_rules();
	}

	private function __construct() {
		add_action('init', [$this, 'register_post_type']);
		add_action('init', [$this, 'maybe_create_page']);
		add_action('init', [$this, 'maybe_register_private_directory'], 20);
		$this->grant_review_capability();
	}

	public function register_post_type(): void {
		register_post_type(self::CPT, [
			'labels' => [
				'name' => 'Padel Video Analyses',
				'singular_name' => 'Padel Video Analysis',
				'add_new_item' => 'Add New Analysis',
				'edit_item' => 'Edit Analysis',
				'new_item' => 'New Analysis',
				'menu_name' => 'Padel Analysis',
			],
			'public' => false,
			'publicly_queryable' => false,
			'exclude_from_search' => true,
			'show_ui' => true,
			'show_in_menu' => false,
			'show_in_rest' => false,
			'supports' => ['title'],
			'capability_type' => 'post',
			'map_meta_cap' => true,
			'rewrite' => false,
		]);
	}

	public function maybe_create_page(bool $force = false): int {
		$page_id = (int) get_option(self::PAGE_OPTION, 0);
		$page = $page_id > 0 ? get_post($page_id) : null;

		if (!$page instanceof WP_Post || $page->post_type !== 'page' || $force) {
			$existing = get_page_by_path(self::PAGE_SLUG);
			if ($existing instanceof WP_Post) {
				$page_id = (int) $existing->ID;
				wp_update_post([
					'ID' => $page_id,
					'post_title' => self::PAGE_TITLE,
					'post_status' => 'publish',
					'post_content' => '[' . Padel_Video_Analysis_Shortcode::SHORTCODE_TAG . ']',
				]);
			} else {
				$page_id = (int) wp_insert_post([
					'post_type' => 'page',
					'post_status' => 'publish',
					'post_title' => self::PAGE_TITLE,
					'post_name' => self::PAGE_SLUG,
					'post_content' => '[' . Padel_Video_Analysis_Shortcode::SHORTCODE_TAG . ']',
				]);
			}

			if ($page_id > 0) {
				update_option(self::PAGE_OPTION, $page_id, false);
			}
		}

		return $page_id;
	}

	public function get_page_url(): string {
		$page_id = (int) get_option(self::PAGE_OPTION, 0);
		if ($page_id > 0) {
			$url = get_permalink($page_id);
			if (is_string($url) && $url !== '') {
				return $url;
			}
		}

		return home_url('/' . self::PAGE_SLUG . '/');
	}

	public function get_status_labels(): array {
		return [
			self::STATUS_UPLOADED => 'Uploaded',
			self::STATUS_PROCESSING => 'Processing',
			'awaiting_player_selection' => 'Awaiting player selection',
			self::STATUS_COMPLETED => 'Completed',
			self::STATUS_FAILED => 'Failed',
		];
	}

	public function get_allowed_shot_types(): array {
		return [
			'forehand' => 'Forehand',
		];
	}

	public function get_allowed_hands(): array {
		return [
			'right' => 'Right-handed',
			'left' => 'Left-handed',
		];
	}

	public function get_allowed_camera_angles(): array {
		return [
			'baseline' => 'Baseline',
			'side' => 'Side',
			'rear' => 'Rear',
		];
	}

	public function can_current_user_review(): bool {
		return current_user_can('manage_options') || current_user_can(self::REVIEW_CAPABILITY);
	}

	public function get_review_rule_catalog(): array {
		$path = PADEL_VIDEO_ANALYSIS_PATH . 'padel-analysis-service/config/technical-feedback-rules.json';
		if (!file_exists($path)) {
			return [
				'version' => '',
				'rules' => [],
			];
		}

		$decoded = json_decode((string) file_get_contents($path), true);
		if (!is_array($decoded)) {
			return [
				'version' => '',
				'rules' => [],
			];
		}

		$rules = [];
		foreach (($decoded['rules'] ?? []) as $rule) {
			if (!is_array($rule)) {
				continue;
			}
			$rules[] = [
				'id' => (string) ($rule['id'] ?? ''),
				'title' => (string) ($rule['title'] ?? ''),
				'family' => (string) ($rule['family'] ?? ''),
				'metric' => (string) ($rule['metric'] ?? ''),
				'explanation' => (string) ($rule['explanation'] ?? ''),
				'output_kind' => (string) ($rule['output_kind'] ?? 'finding'),
				'supported_camera_angles' => array_values(array_filter(array_map('strval', (array) ($rule['supported_camera_angles'] ?? [])))),
			];
		}

		return [
			'version' => (string) ($decoded['version'] ?? ''),
			'rules' => $rules,
		];
	}

	public function get_user_analysis_posts(int $user_id, int $limit = 10, string $analysis_mode = 'stroke'): array {
		$posts = get_posts([
			'post_type' => self::CPT,
			'post_status' => ['publish', 'private', 'draft'],
			'author' => $user_id,
			'numberposts' => -1,
			'orderby' => 'date',
			'order' => 'DESC',
		]);

		if (!is_array($posts)) {
			return [];
		}

		$filtered = [];
		foreach ($posts as $post) {
			if (!$post instanceof WP_Post) {
				continue;
			}
			$post_mode = (string) $this->get_meta((int) $post->ID, 'analysis_mode', 'stroke');
			if ($analysis_mode === 'stroke') {
				if ($post_mode === 'match') {
					continue;
				}
			} elseif ($post_mode !== $analysis_mode) {
				continue;
			}
			$filtered[] = $this->maybe_advance_analysis((int) $post->ID);
			if (count($filtered) >= $limit) {
				break;
			}
		}

		return array_values(array_filter($filtered));
	}

	public function get_recent_analysis_posts(int $limit = 10, string $analysis_mode = 'stroke', array $statuses = ['completed']): array {
		$posts = get_posts([
			'post_type' => self::CPT,
			'post_status' => $statuses,
			'numberposts' => -1,
			'orderby' => 'date',
			'order' => 'DESC',
		]);

		if (!is_array($posts)) {
			return [];
		}

		$filtered = [];
		foreach ($posts as $post) {
			if (!$post instanceof WP_Post) {
				continue;
			}
			$post_mode = (string) $this->get_meta((int) $post->ID, 'analysis_mode', 'stroke');
			if ($analysis_mode === 'stroke') {
				if ($post_mode === 'match') {
					continue;
				}
			} elseif ($post_mode !== $analysis_mode) {
				continue;
			}
			$filtered[] = $this->maybe_advance_analysis((int) $post->ID);
			if (count($filtered) >= $limit) {
				break;
			}
		}

		return array_values(array_filter($filtered));
	}

	public function maybe_get_user_analysis(int $user_id, int $analysis_id, ?string $analysis_mode = null): ?WP_Post {
		$post = get_post($analysis_id);
		if (!$post instanceof WP_Post || $post->post_type !== self::CPT) {
			return null;
		}

		if ((int) $post->post_author !== $user_id) {
			return null;
		}

		if ($analysis_mode !== null) {
			$post_mode = (string) $this->get_meta($analysis_id, 'analysis_mode', 'stroke');
			if ($analysis_mode === 'stroke' && $post_mode === 'match') {
				return null;
			}
			if ($analysis_mode !== 'stroke' && $analysis_mode !== $post_mode) {
				return null;
			}
		}

		return $this->maybe_advance_analysis($analysis_id);
	}

	public function maybe_advance_analysis(int $analysis_id): ?WP_Post {
		$post = get_post($analysis_id);
		if (!$post instanceof WP_Post || $post->post_type !== self::CPT) {
			return null;
		}

		$external_client = Padel_Video_Analysis_External_Client::instance();
		if ($external_client->is_enabled()) {
			return $this->sync_external_job($analysis_id);
		}

		$status = $this->get_meta($analysis_id, 'status', self::STATUS_UPLOADED);
		$next_transition_at = (int) $this->get_meta($analysis_id, 'next_transition_at', 0);
		$now = time();

		if ($status === self::STATUS_UPLOADED && $next_transition_at > 0 && $now >= $next_transition_at) {
			$this->set_status($analysis_id, self::STATUS_PROCESSING, [
				'processing_started_at' => gmdate('c'),
				'analysis_engine' => Padel_Video_Analysis_Engine::ENGINE_NAME,
				'analysis_engine_version' => Padel_Video_Analysis_Engine::ENGINE_VERSION,
				'next_transition_at' => $now + 3,
			]);
			$status = self::STATUS_PROCESSING;
			$next_transition_at = $now + 3;
		}

		if ($status === self::STATUS_PROCESSING && $next_transition_at > 0 && $now >= $next_transition_at) {
			$engine_result = Padel_Video_Analysis_Engine::instance()->process($analysis_id);
			if (is_wp_error($engine_result)) {
				$this->set_status($analysis_id, self::STATUS_FAILED, [
					'completed_at' => gmdate('c'),
					'failure_reason' => $engine_result->get_error_message(),
					'next_transition_at' => 0,
				]);
			} else {
				$this->set_status($analysis_id, self::STATUS_COMPLETED, [
					'completed_at' => gmdate('c'),
					'analysis_result' => $engine_result['result'] ?? [],
					'normalized_video_path' => $engine_result['normalized_video_path'] ?? '',
					'poster_frame_path' => $engine_result['poster_frame_path'] ?? '',
					'analysis_engine' => $engine_result['engine_name'] ?? Padel_Video_Analysis_Engine::ENGINE_NAME,
					'analysis_engine_version' => $engine_result['engine_version'] ?? Padel_Video_Analysis_Engine::ENGINE_VERSION,
					'analysis_probe' => $engine_result['probe'] ?? [],
					'next_transition_at' => 0,
				]);
			}
		}

		return get_post($analysis_id) ?: null;
	}

	public function create_analysis(int $user_id, array $payload, string $analysis_mode = 'stroke'): WP_Post|WP_Error {
		$user = get_user_by('id', $user_id);
		if (!$user instanceof WP_User) {
			return new WP_Error('padel_invalid_user', 'Invalid user.', ['status' => 400]);
		}

		$analysis_mode = $analysis_mode === 'match' ? 'match' : 'stroke';
		$shot_type = $this->sanitize_choice($payload['shot_type'] ?? '', array_keys($this->get_allowed_shot_types()));
		$dominant_hand = $this->sanitize_choice($payload['dominant_hand'] ?? '', array_keys($this->get_allowed_hands()));
		$camera_angle = $this->sanitize_choice($payload['camera_angle'] ?? '', array_keys($this->get_allowed_camera_angles()));

		if ($shot_type === '' || $dominant_hand === '' || $camera_angle === '') {
			return new WP_Error('padel_invalid_payload', 'Missing or invalid form values.', ['status' => 400]);
		}

		$title = sprintf('Padel analysis - %s - %s', $user->display_name ?: $user->user_login, current_time('mysql'));
		$post_id = wp_insert_post([
			'post_type' => self::CPT,
			'post_status' => 'publish',
			'post_title' => $title,
			'post_author' => $user_id,
		], true);

		if (is_wp_error($post_id)) {
			return $post_id;
		}

		$post_id = (int) $post_id;
		$processor = Padel_Video_Analysis_External_Client::instance()->is_enabled() ? 'external_python_service' : self::PROCESSOR_LOCAL;
		$this->set_meta($post_id, 'owner_user_id', $user_id);
		$this->set_meta($post_id, 'analysis_mode', $analysis_mode);
		$this->set_meta($post_id, 'shot_type', $shot_type);
		$this->set_meta($post_id, 'dominant_hand', $dominant_hand);
		$this->set_meta($post_id, 'camera_angle', $camera_angle);
		$this->set_meta($post_id, 'status', self::STATUS_UPLOADED);
		$this->set_meta($post_id, 'processor', $processor);
		$this->set_meta($post_id, 'analysis_engine', $processor);
		$this->set_meta($post_id, 'analysis_engine_version', $processor === self::PROCESSOR_LOCAL ? Padel_Video_Analysis_Engine::ENGINE_VERSION : '0.1.0');
		$this->set_meta($post_id, 'next_transition_at', time() + 1);
		$this->set_meta($post_id, 'created_at', gmdate('c'));
		$this->set_meta($post_id, 'analysis_result', []);
		$this->set_meta($post_id, 'normalized_video_path', '');
		$this->set_meta($post_id, 'poster_frame_path', '');
		$this->set_meta($post_id, 'analysis_probe', []);
		$this->set_meta($post_id, 'submission_fingerprint', '');
		$this->set_meta($post_id, 'external_job_id', '');
		$this->set_meta($post_id, 'external_status', '');
		$this->set_meta($post_id, 'external_last_synced_at', '');
		$this->set_meta($post_id, 'external_error_code', '');
		$this->set_meta($post_id, 'external_error_message', '');
		$this->set_meta($post_id, 'external_error_details', []);
		$this->set_meta($post_id, 'external_retry_count', 0);
		$this->set_meta($post_id, 'external_next_retry_at', 0);
		$this->set_meta($post_id, 'download_token_hash', '');
		$this->set_meta($post_id, 'download_token_expires_at', 0);
		$this->set_meta($post_id, 'download_token_used_at', '');
		$this->set_meta($post_id, 'download_token_job_id', '');
		$this->set_meta($post_id, 'manual_match_reviews', []);
		$this->set_meta($post_id, 'selected_player_candidate_id', '');

		return get_post($post_id) ?: new WP_Error('padel_record_create_failed', 'Unable to create analysis record.', ['status' => 500]);
	}

	public function attach_video_to_analysis(int $analysis_id, array $file_result): void {
		$this->set_meta($analysis_id, 'video_path', $file_result['path'] ?? '');
		$this->set_meta($analysis_id, 'video_name', $file_result['name'] ?? '');
		$this->set_meta($analysis_id, 'video_mime_type', $file_result['mime_type'] ?? '');
		$this->set_meta($analysis_id, 'video_size', (int) ($file_result['size'] ?? 0));
		$this->set_meta($analysis_id, 'video_duration_seconds', (float) ($file_result['duration_seconds'] ?? 0));
		$this->set_meta($analysis_id, 'video_sha256', $file_result['sha256'] ?? '');
	}

	public function delete_analysis(int $analysis_id): bool {
		$post = get_post($analysis_id);
		if (!$post instanceof WP_Post || $post->post_type !== self::CPT) {
			return false;
		}

		Padel_Video_Analysis_Cron::unschedule_analysis($analysis_id);

		$storage = Padel_Video_Analysis_Storage::instance();
		$storage->delete_private_file((string) $this->get_meta($analysis_id, 'video_path', ''));
		$storage->delete_private_file((string) $this->get_meta($analysis_id, 'normalized_video_path', ''));
		$storage->delete_private_file((string) $this->get_meta($analysis_id, 'poster_frame_path', ''));
		$storage->delete_artifact_cache($analysis_id);
		$external_job_id = (string) $this->get_meta($analysis_id, 'external_job_id', '');
		if ($external_job_id !== '' && Padel_Video_Analysis_External_Client::instance()->is_enabled()) {
			Padel_Video_Analysis_External_Client::instance()->delete_job($external_job_id);
		}
		Padel_Video_Analysis_Review_Store::instance()->delete_reviews_for_analysis($analysis_id);
		return (bool) wp_delete_post($analysis_id, true);
	}

	public function get_record_payload(WP_Post $post): array {
		$analysis_id = (int) $post->ID;
		$post = $this->maybe_advance_analysis($analysis_id) ?: $post;
		$status = (string) $this->get_meta($analysis_id, 'status', self::STATUS_UPLOADED);
		$result = $this->get_meta($analysis_id, 'analysis_result', []);
		if (!is_array($result) || empty($result)) {
			$result = $this->get_meta($analysis_id, 'result_payload', []);
		}
		if (!is_array($result)) {
			$result = [];
		}

		$shot_type = (string) $this->get_meta($analysis_id, 'shot_type', 'forehand');
		$dominant_hand = (string) $this->get_meta($analysis_id, 'dominant_hand', 'right');
		$camera_angle = (string) $this->get_meta($analysis_id, 'camera_angle', 'baseline');
		$analysis_mode = (string) $this->get_meta($analysis_id, 'analysis_mode', 'stroke');
		$video_size = (int) $this->get_meta($analysis_id, 'video_size', 0);
		$video_duration_seconds = (float) $this->get_meta($analysis_id, 'video_duration_seconds', 0);
		$manual_reviews = $this->get_meta($analysis_id, 'manual_match_reviews', []);
		$selected_player_candidate_id = (string) $this->get_meta($analysis_id, 'selected_player_candidate_id', '');
		$review = null;
		if ($this->can_current_user_review()) {
			$review_rules_version = Padel_Video_Analysis_Review_Store::instance()->get_latest_review_rules_version($analysis_id);
			if ($review_rules_version === '') {
				$review_rules_version = (string) ($result['technical_feedback']['rules_version'] ?? '');
			}
			$review = Padel_Video_Analysis_Review_Store::instance()->get_review(
				$analysis_id,
				$review_rules_version !== '' ? $review_rules_version : null
			);
		}

		$payload = [
			'id' => $analysis_id,
			'title' => get_the_title($post),
			'author_id' => (int) $post->post_author,
			'created_at' => get_post_time('c', true, $post),
			'status' => $status,
			'status_label' => $this->get_status_labels()[$status] ?? ucfirst($status),
			'progress' => $this->get_status_progress($status),
			'analysis_mode' => $analysis_mode,
			'analysis_mode_label' => $analysis_mode === 'match' ? 'Match analysis' : 'Single stroke',
			'shot_type' => $shot_type,
			'shot_type_label' => $this->get_allowed_shot_types()[$shot_type] ?? $shot_type,
			'dominant_hand' => $dominant_hand,
			'dominant_hand_label' => $this->get_allowed_hands()[$dominant_hand] ?? $dominant_hand,
			'camera_angle' => $camera_angle,
			'camera_angle_label' => $this->get_allowed_camera_angles()[$camera_angle] ?? $camera_angle,
			'video' => [
				'name' => (string) $this->get_meta($analysis_id, 'video_name', ''),
				'mime_type' => (string) $this->get_meta($analysis_id, 'video_mime_type', ''),
				'size' => $video_size,
				'size_human' => size_format($video_size),
				'duration_seconds' => $video_duration_seconds,
			],
			'result' => $result,
			'manual_reviews' => is_array($manual_reviews) ? $manual_reviews : [],
			'selected_player_candidate_id' => $selected_player_candidate_id,
			'processor' => (string) $this->get_meta($analysis_id, 'processor', self::PROCESSOR_LOCAL),
			'analysis_engine' => (string) $this->get_meta($analysis_id, 'analysis_engine', ''),
			'analysis_engine_version' => (string) $this->get_meta($analysis_id, 'analysis_engine_version', ''),
			'external' => [
				'job_id' => (string) $this->get_meta($analysis_id, 'external_job_id', ''),
				'status' => (string) $this->get_meta($analysis_id, 'external_status', ''),
				'last_synced_at' => (string) $this->get_meta($analysis_id, 'external_last_synced_at', ''),
				'error' => [
					'code' => (string) $this->get_meta($analysis_id, 'external_error_code', ''),
					'message' => (string) $this->get_meta($analysis_id, 'external_error_message', ''),
					'details' => $this->get_meta($analysis_id, 'external_error_details', []),
				],
				'retry_count' => (int) $this->get_meta($analysis_id, 'external_retry_count', 0),
				'next_retry_at' => (int) $this->get_meta($analysis_id, 'external_next_retry_at', 0),
			],
			'artifacts' => [
				'normalized_video_path' => (string) $this->get_meta($analysis_id, 'normalized_video_path', ''),
				'poster_frame_path' => (string) $this->get_meta($analysis_id, 'poster_frame_path', ''),
			],
			'processing_started_at' => (string) $this->get_meta($analysis_id, 'processing_started_at', ''),
			'completed_at' => (string) $this->get_meta($analysis_id, 'completed_at', ''),
			'failure_reason' => (string) $this->get_meta($analysis_id, 'failure_reason', ''),
			'manual_contact_frame_index' => ($manual_contact_frame = (int) $this->get_meta($analysis_id, 'manual_contact_frame_index', 0)) > 0 ? $manual_contact_frame : null,
			'manual_contact_frame_source' => (string) $this->get_meta($analysis_id, 'manual_contact_frame_source', ''),
			'manual_contact_frame_updated_at' => (string) $this->get_meta($analysis_id, 'manual_contact_frame_updated_at', ''),
			'can_delete' => (int) $post->post_author === get_current_user_id(),
		];

		if ($review !== null && $review !== []) {
			$payload['review'] = $review;
			$payload['review_version'] = (string) ($review['rules_version'] ?? '');
		}

		return $payload;
	}

	public function get_status_progress(string $status): int {
		return match ($status) {
			self::STATUS_UPLOADED => 24,
			self::STATUS_PROCESSING => 64,
			'awaiting_player_selection' => 48,
			self::STATUS_COMPLETED => 100,
			self::STATUS_FAILED => 100,
			default => 0,
		};
	}

	public function sanitize_choice(mixed $value, array $allowed): string {
		$choice = sanitize_key((string) $value);
		return in_array($choice, $allowed, true) ? $choice : '';
	}

	public function get_automatic_result_checksum(array $result): string {
		return hash('sha256', wp_json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
	}

	public function save_review(int $analysis_id, array $payload, ?string $rules_version = null): array|WP_Error {
		$post = get_post($analysis_id);
		if (!$post instanceof WP_Post || $post->post_type !== self::CPT) {
			return new WP_Error('padel_invalid_analysis', 'Analysis record not found.', ['status' => 404]);
		}

		if (!$this->can_current_user_review()) {
			return new WP_Error('padel_forbidden', 'Reviewer access is required.', ['status' => 403]);
		}

		$record = $this->get_record_payload($post);
		$result = is_array($record['result'] ?? null) ? $record['result'] : [];
		$technical_feedback = is_array($result['technical_feedback'] ?? null) ? $result['technical_feedback'] : [];
		$resolved_rules_version = $rules_version !== null && $rules_version !== '' ? $rules_version : (string) ($technical_feedback['rules_version'] ?? '');
		if ($resolved_rules_version === '') {
			$resolved_rules_version = (string) $this->get_review_rule_catalog()['version'];
		}
		if ($resolved_rules_version === '') {
			return new WP_Error('padel_missing_rules_version', 'Rules version is required.', ['status' => 400]);
		}

		$rule_catalog = $this->get_review_rule_catalog();
		$catalog_ids = array_values(array_filter(array_map(static fn(array $rule) => (string) ($rule['id'] ?? ''), (array) ($rule_catalog['rules'] ?? []))));

		$items = [];
		$automatic_items = $this->build_review_items_from_technical_feedback($analysis_id, $technical_feedback);
		$automatic_item_map = [];
		foreach ($automatic_items as $item) {
			$automatic_item_map[$item['rule_id']] = $item;
		}

		foreach ((array) ($payload['items'] ?? []) as $item) {
			if (!is_array($item)) {
				continue;
			}
			$item_kind = sanitize_key((string) ($item['item_kind'] ?? ''));
			$rule_id = sanitize_key((string) ($item['rule_id'] ?? ''));
			if ($item_kind === '' || $rule_id === '') {
				continue;
			}
			if ($item_kind !== 'manual_missed_finding' && !isset($automatic_item_map[$rule_id])) {
				return new WP_Error('padel_invalid_review_rule', 'Unsupported review rule id.', ['status' => 400]);
			}
			if ($item_kind === 'manual_missed_finding' && !in_array($rule_id, $catalog_ids, true)) {
				return new WP_Error('padel_invalid_review_rule', 'Unsupported manual rule id.', ['status' => 400]);
			}

			$items[] = [
				'item_kind' => $item_kind,
				'rule_id' => $rule_id,
				'title' => (string) ($item['title'] ?? ($automatic_item_map[$rule_id]['title'] ?? $rule_id)),
				'review_label' => sanitize_key((string) ($item['review_label'] ?? '')),
				'severity' => sanitize_key((string) ($item['severity'] ?? '')),
				'reviewer_note' => sanitize_textarea_field((string) ($item['reviewer_note'] ?? '')),
				'timestamp_correct' => $this->normalize_review_bool($item['timestamp_correct'] ?? null),
				'wording_correct' => $this->normalize_review_bool($item['wording_correct'] ?? null),
				'should_have_been_withheld' => $this->normalize_review_bool($item['should_have_been_withheld'] ?? null),
				'measurement_reasonable' => $this->normalize_review_bool($item['measurement_reasonable'] ?? null),
				'manual_miss_kind' => sanitize_key((string) ($item['manual_miss_kind'] ?? '')),
				'manual_rule_id' => sanitize_key((string) ($item['manual_rule_id'] ?? $rule_id)),
				'confidence_level' => sanitize_key((string) ($item['confidence_level'] ?? ($automatic_item_map[$rule_id]['confidence_level'] ?? ''))),
				'supporting_timestamp_ms' => isset($item['supporting_timestamp_ms']) && is_numeric($item['supporting_timestamp_ms']) ? (int) $item['supporting_timestamp_ms'] : ($automatic_item_map[$rule_id]['supporting_timestamp_ms'] ?? null),
				'supporting_frame_index' => isset($item['supporting_frame_index']) && is_numeric($item['supporting_frame_index']) ? (int) $item['supporting_frame_index'] : ($automatic_item_map[$rule_id]['supporting_frame_index'] ?? null),
				'item_snapshot' => $automatic_item_map[$rule_id]['snapshot'] ?? [],
			];
		}

		foreach ((array) ($payload['manual_missed_findings'] ?? []) as $item) {
			if (!is_array($item)) {
				continue;
			}
			$manual_rule_id = sanitize_key((string) ($item['manual_rule_id'] ?? ''));
			if ($manual_rule_id === '' || !in_array($manual_rule_id, $catalog_ids, true)) {
				return new WP_Error('padel_invalid_review_rule', 'Unsupported manual rule id.', ['status' => 400]);
			}
			$items[] = [
				'item_kind' => 'manual_missed_finding',
				'rule_id' => $manual_rule_id,
				'title' => (string) ($item['title'] ?? $manual_rule_id),
				'review_label' => sanitize_key((string) ($item['review_label'] ?? 'missed')),
				'severity' => sanitize_key((string) ($item['severity'] ?? '')),
				'reviewer_note' => sanitize_textarea_field((string) ($item['reviewer_note'] ?? '')),
				'timestamp_correct' => null,
				'wording_correct' => null,
				'should_have_been_withheld' => null,
				'measurement_reasonable' => null,
				'manual_miss_kind' => sanitize_key((string) ($item['manual_miss_kind'] ?? 'missed_completely')),
				'manual_rule_id' => $manual_rule_id,
				'confidence_level' => sanitize_key((string) ($item['confidence_level'] ?? 'low')),
				'supporting_timestamp_ms' => isset($item['supporting_timestamp_ms']) && is_numeric($item['supporting_timestamp_ms']) ? (int) $item['supporting_timestamp_ms'] : null,
				'supporting_frame_index' => isset($item['supporting_frame_index']) && is_numeric($item['supporting_frame_index']) ? (int) $item['supporting_frame_index'] : null,
				'item_snapshot' => [],
			];
		}

		$overall_video_suitability = sanitize_text_field((string) ($payload['overall_video_suitability'] ?? ''));
		$overall_analysis_usefulness = sanitize_text_field((string) ($payload['overall_analysis_usefulness'] ?? ''));
		$automatic_checksum = $this->get_automatic_result_checksum($result);

		return Padel_Video_Analysis_Review_Store::instance()->upsert_review(
			$analysis_id,
			$resolved_rules_version,
			$automatic_checksum,
			get_current_user_id(),
			[
				'pose_confidence' => (string) ($result['pose']['confidence_level'] ?? ''),
				'stroke_confidence' => (string) ($result['stroke']['confidence_level'] ?? ''),
				'camera_angle' => (string) ($record['camera_angle'] ?? ''),
				'full_body_visible' => (bool) ($result['pose']['full_body_visible'] ?? false),
				'dominant_hand' => (string) ($record['dominant_hand'] ?? ''),
				'overall_video_suitability' => in_array($overall_video_suitability, ['excellent', 'usable', 'limited', 'unusable'], true) ? $overall_video_suitability : '',
				'overall_analysis_usefulness' => in_array($overall_analysis_usefulness, ['useful', 'partly useful', 'misleading', 'insufficient'], true) ? $overall_analysis_usefulness : '',
				'automatic_result_snapshot' => $result,
			],
			$items
		);
	}

	public function get_review_calibration_summary(?string $rules_version = null): array {
		return Padel_Video_Analysis_Review_Store::instance()->get_calibration_summary($rules_version);
	}

	private function grant_review_capability(): void {
		$role = get_role('administrator');
		if ($role instanceof WP_Role) {
			$role->add_cap(self::REVIEW_CAPABILITY);
		}
	}

	private function normalize_review_bool(mixed $value): ?bool {
		if ($value === null || $value === '') {
			return null;
		}
		return filter_var($value, FILTER_VALIDATE_BOOL, FILTER_NULL_ON_FAILURE);
	}

	private function build_review_items_from_technical_feedback(int $analysis_id, array $technical_feedback): array {
		$items = [];
		$append = static function (array &$items, array $source, string $kind): void {
			foreach ($source as $item) {
				if (!is_array($item)) {
					continue;
				}
				$rule_id = sanitize_key((string) ($item['rule_id'] ?? ''));
				if ($rule_id === '') {
					continue;
				}
				$items[] = [
					'item_kind' => $kind,
					'rule_id' => $rule_id,
					'title' => (string) ($item['title'] ?? $rule_id),
					'confidence_level' => sanitize_key((string) ($item['confidence_level'] ?? '')),
					'supporting_timestamp_ms' => isset($item['primary_timestamp_ms']) && is_numeric($item['primary_timestamp_ms']) ? (int) $item['primary_timestamp_ms'] : null,
					'supporting_frame_index' => isset($item['primary_frame_index']) && is_numeric($item['primary_frame_index']) ? (int) $item['primary_frame_index'] : null,
					'snapshot' => $item,
				];
			}
		};

		$append($items, (array) ($technical_feedback['findings'] ?? []), 'finding');
		$append($items, (array) ($technical_feedback['possible_observations'] ?? []), 'possible_observation');
		$append($items, (array) ($technical_feedback['neutral_measurements'] ?? []), 'neutral_measurement');
		$append($items, (array) ($technical_feedback['withheld_findings'] ?? []), 'withheld_finding');

		$map = [];
		foreach ($items as $item) {
			$map[$item['rule_id']] = $item;
		}

		return $map;
	}

	public function set_status(int $analysis_id, string $status, array $extra_meta = []): void {
		$this->set_meta($analysis_id, 'status', $status);
		foreach ($extra_meta as $key => $value) {
			$this->set_meta($analysis_id, (string) $key, $value);
		}
	}

	public function get_meta(int $analysis_id, string $key, mixed $default = ''): mixed {
		$value = get_post_meta($analysis_id, 'padel_video_analysis_' . $key, true);
		return $value === '' ? $default : $value;
	}

	public function set_meta(int $analysis_id, string $key, mixed $value): void {
		update_post_meta($analysis_id, 'padel_video_analysis_' . $key, $value);
	}

	public function maybe_register_private_directory(): void {
		Padel_Video_Analysis_Storage::instance()->ensure_private_directory();
	}

	public function build_private_video_download_url(int $analysis_id): string|WP_Error {
		$issued = $this->issue_private_download_token($analysis_id);
		if (is_wp_error($issued)) {
			return $issued;
		}

		return add_query_arg(
			['token' => rawurlencode((string) $issued['token'])],
			rest_url('padel-video-analysis/v1/private-download/' . $analysis_id)
		);
	}

	public function issue_private_download_token(int $analysis_id): array|WP_Error {
		$post = get_post($analysis_id);
		if (!$post instanceof WP_Post || $post->post_type !== self::CPT) {
			return new WP_Error('padel_invalid_analysis', 'Analysis record not found.', ['status' => 404]);
		}

		$video_path = (string) $this->get_meta($analysis_id, 'video_path', '');
		if ($video_path === '' || !file_exists($video_path)) {
			return new WP_Error('padel_missing_video', 'Video file is missing.', ['status' => 404]);
		}

		try {
			$token = bin2hex(random_bytes(32));
		} catch (Throwable $exception) {
			return new WP_Error('padel_download_token_failed', 'Unable to create a private download token.', [
				'status' => 500,
				'detail' => $exception->getMessage(),
			]);
		}

		$expires_at = time() + self::DOWNLOAD_TOKEN_TTL_SECONDS;
		$this->set_meta($analysis_id, 'download_token_hash', hash('sha256', $token));
		$this->set_meta($analysis_id, 'download_token_expires_at', $expires_at);
		$this->set_meta($analysis_id, 'download_token_used_at', '');
		$this->set_meta($analysis_id, 'download_token_job_id', (string) $this->get_meta($analysis_id, 'external_job_id', ''));

		return [
			'token' => $token,
			'expires_at' => $expires_at,
		];
	}

	public function validate_private_download_token(int $analysis_id, string $provided_token): WP_Error|true {
		if ($provided_token === '') {
			return new WP_Error('padel_download_token_missing', 'A private download token is required.', ['status' => 401]);
		}

		$stored_hash = (string) $this->get_meta($analysis_id, 'download_token_hash', '');
		if ($stored_hash === '') {
			return new WP_Error('padel_download_token_missing', 'A private download token is required.', ['status' => 401]);
		}

		$expected_job_id = (string) $this->get_meta($analysis_id, 'download_token_job_id', '');
		$current_job_id = (string) $this->get_meta($analysis_id, 'external_job_id', '');
		if ($expected_job_id !== '' && $current_job_id !== '' && !hash_equals($expected_job_id, $current_job_id)) {
			return new WP_Error('padel_download_token_mismatch', 'The private download token does not match this analysis.', ['status' => 403]);
		}

		$used_at = (string) $this->get_meta($analysis_id, 'download_token_used_at', '');
		if ($used_at !== '') {
			return new WP_Error('padel_download_token_used', 'The private download token has already been used.', ['status' => 403]);
		}

		$expires_at = (int) $this->get_meta($analysis_id, 'download_token_expires_at', 0);
		if ($expires_at > 0 && time() > $expires_at) {
			return new WP_Error('padel_download_token_expired', 'The private download token has expired.', ['status' => 403]);
		}

		$provided_hash = hash('sha256', $provided_token);
		if (!hash_equals($stored_hash, $provided_hash)) {
			return new WP_Error('padel_download_token_invalid', 'The private download token is invalid.', ['status' => 403]);
		}

		return true;
	}

	public function mark_private_download_token_used(int $analysis_id): void {
		$this->set_meta($analysis_id, 'download_token_used_at', gmdate('c'));
		$this->set_meta($analysis_id, 'download_token_hash', '');
	}

	public function build_submission_fingerprint(int $user_id, string $analysis_mode, string $shot_type, string $dominant_hand, string $camera_angle, string $video_sha256): string {
		return hash('sha256', implode('|', [$user_id, $analysis_mode, $shot_type, $dominant_hand, $camera_angle, $video_sha256]));
	}

	public function find_existing_analysis_by_fingerprint(string $fingerprint, int $exclude_id = 0): ?WP_Post {
		$query_args = [
			'post_type' => self::CPT,
			'post_status' => ['publish', 'private', 'draft'],
			'numberposts' => 1,
			'fields' => 'all',
			'meta_key' => 'padel_video_analysis_submission_fingerprint',
			'meta_value' => $fingerprint,
		];
		if ($exclude_id > 0) {
			$query_args['post__not_in'] = [$exclude_id];
		}
		$posts = get_posts($query_args);

		foreach ($posts as $post) {
			if ($post instanceof WP_Post) {
				return $post;
			}
		}

		return null;
	}

	public function build_external_job_payload(int $analysis_id): WP_Error|array {
		return Padel_Video_Analysis_External_Client::instance()->build_job_payload($analysis_id);
	}

	public function submit_external_job(int $analysis_id, bool $force = false): array|WP_Error {
		$client = Padel_Video_Analysis_External_Client::instance();
		if (!$client->is_enabled()) {
			return new WP_Error('padel_external_service_disabled', 'External analysis service is not configured.', ['status' => 500]);
		}

		if (!$force) {
			$job_id = (string) $this->get_meta($analysis_id, 'external_job_id', '');
			if ($job_id !== '') {
				$this->set_meta($analysis_id, 'external_status', 'queued');
				return ['job_id' => $job_id, 'status' => (string) $this->get_meta($analysis_id, 'external_status', 'queued')];
			}
		}

		$payload = $this->build_external_job_payload($analysis_id);
		if (is_wp_error($payload)) {
			return $payload;
		}

		$response = $client->request_json('POST', '/api/v1/jobs', $payload);
		if (is_wp_error($response)) {
			$this->record_external_error($analysis_id, $response);
			$this->increment_external_retry($analysis_id);
			$this->schedule_external_retry($analysis_id);
			return $response;
		}

		$this->set_meta($analysis_id, 'external_job_id', (string) ($response['job_id'] ?? ''));
		$this->set_meta($analysis_id, 'external_status', (string) ($response['status'] ?? 'queued'));
		$this->set_meta($analysis_id, 'external_last_synced_at', gmdate('c'));
		$this->set_meta($analysis_id, 'download_token_job_id', (string) ($response['job_id'] ?? ''));
		$this->clear_external_error($analysis_id);
		$this->set_meta($analysis_id, 'external_retry_count', 0);
		$this->set_meta($analysis_id, 'external_next_retry_at', 0);
		$this->set_status($analysis_id, self::STATUS_PROCESSING, [
			'next_transition_at' => 0,
			'processing_started_at' => gmdate('c'),
			'analysis_engine' => 'external_python_service',
			'analysis_engine_version' => '0.1.0',
		]);

		return $response;
	}

	public function sync_external_job(int $analysis_id): ?WP_Post {
		$client = Padel_Video_Analysis_External_Client::instance();
		if (!$client->is_enabled()) {
			return get_post($analysis_id) ?: null;
		}

		$post = get_post($analysis_id);
		if (!$post instanceof WP_Post || $post->post_type !== self::CPT) {
			return null;
		}

		$current_status = (string) $this->get_meta($analysis_id, 'status', self::STATUS_UPLOADED);
		if (in_array($current_status, [self::STATUS_COMPLETED, self::STATUS_FAILED], true)) {
			return get_post($analysis_id) ?: null;
		}

		$external_job_id = (string) $this->get_meta($analysis_id, 'external_job_id', '');
		if ($external_job_id === '') {
			$next_retry_at = (int) $this->get_meta($analysis_id, 'external_next_retry_at', 0);
			if ($next_retry_at > 0 && time() < $next_retry_at) {
				return get_post($analysis_id) ?: null;
			}

			$this->submit_external_job($analysis_id);
			return get_post($analysis_id) ?: null;
		}

		$response = $client->fetch_job($external_job_id);
		if (is_wp_error($response)) {
			$this->record_external_error($analysis_id, $response);
			$this->increment_external_retry($analysis_id);
			$this->schedule_external_retry($analysis_id);
			return get_post($analysis_id) ?: null;
		}

		$this->apply_external_job_response($analysis_id, $response, $external_job_id);

		return get_post($analysis_id) ?: null;
	}

	public function apply_external_job_response(int $analysis_id, array $response, string $fallback_job_id = ''): void {
		$status = (string) ($response['status'] ?? 'queued');
		$this->set_meta($analysis_id, 'external_status', $status);
		$this->set_meta($analysis_id, 'external_last_synced_at', gmdate('c'));
		$this->set_meta($analysis_id, 'external_job_id', (string) ($response['job_id'] ?? $fallback_job_id));
		$this->set_meta($analysis_id, 'external_error_code', '');
		$this->set_meta($analysis_id, 'external_error_message', '');
		$this->set_meta($analysis_id, 'external_error_details', []);
		$this->set_meta($analysis_id, 'external_retry_count', 0);
		$this->set_meta($analysis_id, 'external_next_retry_at', 0);

		if ($status === 'awaiting_player_selection') {
			$result = is_array($response['result'] ?? null) ? $response['result'] : [];
			$result = $this->normalize_external_result_identity($analysis_id, $result, $response, $fallback_job_id);
			$this->set_status($analysis_id, 'awaiting_player_selection', [
				'completed_at' => '',
				'analysis_result' => $result,
				'next_transition_at' => 0,
				'selected_player_candidate_id' => (string) ($result['match']['selected_player_candidate_id'] ?? $this->get_meta($analysis_id, 'selected_player_candidate_id', '')),
			]);
			return;
		}

		if ($status === 'completed') {
			$result = is_array($response['result'] ?? null) ? $response['result'] : [];
			$result = $this->normalize_external_result_identity($analysis_id, $result, $response, $fallback_job_id);
			$this->set_status($analysis_id, self::STATUS_COMPLETED, [
				'completed_at' => gmdate('c'),
				'analysis_result' => $result,
				'selected_player_candidate_id' => (string) ($result['match']['selected_player_candidate_id'] ?? $this->get_meta($analysis_id, 'selected_player_candidate_id', '')),
				'external_error_code' => '',
				'external_error_message' => '',
				'external_error_details' => [],
				'processor' => 'external_python_service',
				'analysis_engine' => 'padel-analysis-service',
				'analysis_engine_version' => '0.1.0',
				'next_transition_at' => 0,
			]);
			return;
		}

		if ($status === 'failed') {
			$error = is_array($response['error'] ?? null) ? $response['error'] : [];
			$message = (string) ($error['message'] ?? 'External analysis failed.');
			$code = (string) ($error['code'] ?? 'external_failed');
			$this->set_status($analysis_id, self::STATUS_FAILED, [
				'completed_at' => gmdate('c'),
				'failure_reason' => $message,
				'external_error_code' => $code,
				'external_error_message' => $message,
				'external_error_details' => $error,
				'next_transition_at' => 0,
			]);
			return;
		}

		$this->set_status($analysis_id, self::STATUS_PROCESSING, [
			'processing_started_at' => (string) $this->get_meta($analysis_id, 'processing_started_at', gmdate('c')),
			'external_error_code' => '',
			'external_error_message' => '',
			'external_error_details' => [],
			'next_transition_at' => 0,
		]);
	}

	public function select_match_player(int $analysis_id, string $selected_candidate_id): array|WP_Error {
		$client = Padel_Video_Analysis_External_Client::instance();
		if (!$client->is_enabled()) {
			return new WP_Error('padel_external_service_disabled', 'External analysis service is not configured.', ['status' => 500]);
		}

		$job_id = (string) $this->get_meta($analysis_id, 'external_job_id', '');
		if ($job_id === '') {
			return new WP_Error('padel_external_job_missing', 'External job is not available.', ['status' => 409]);
		}

		$fresh_download_url = $this->build_private_video_download_url($analysis_id);
		if (is_wp_error($fresh_download_url)) {
			return $fresh_download_url;
		}

		$this->set_meta($analysis_id, 'selected_player_candidate_id', $selected_candidate_id);
		$this->set_meta($analysis_id, 'download_token_job_id', $job_id);
		$response = $client->select_match_player($job_id, $selected_candidate_id, $fresh_download_url);
		if (is_wp_error($response)) {
			$this->record_external_error($analysis_id, $response);
			return $response;
		}

		$this->apply_external_job_response($analysis_id, $response, $job_id);
		return $response;
	}

	public function record_external_error(int $analysis_id, WP_Error $error): void {
		$this->set_meta($analysis_id, 'external_status', 'failed');
		$this->set_meta($analysis_id, 'external_error_code', (string) $error->get_error_code());
		$this->set_meta($analysis_id, 'external_error_message', $error->get_error_message());
		$this->set_meta($analysis_id, 'external_error_details', $error->get_error_data());
		$this->set_meta($analysis_id, 'external_last_synced_at', gmdate('c'));
	}

	public function increment_external_retry(int $analysis_id): void {
		$retry_count = (int) $this->get_meta($analysis_id, 'external_retry_count', 0) + 1;
		$this->set_meta($analysis_id, 'external_retry_count', $retry_count);
		$backoff = min(300, 5 * (2 ** max(0, $retry_count - 1)));
		$this->set_meta($analysis_id, 'external_next_retry_at', time() + $backoff);
	}

	public function schedule_external_retry(int $analysis_id): void {
		$retry_count = (int) $this->get_meta($analysis_id, 'external_retry_count', 0);
		wp_schedule_single_event(time() + min(300, 5 * max(1, $retry_count)), 'padel_video_analysis_submit_external_job', [$analysis_id]);
	}

	public function clear_external_error(int $analysis_id): void {
		$this->set_meta($analysis_id, 'external_error_code', '');
		$this->set_meta($analysis_id, 'external_error_message', '');
		$this->set_meta($analysis_id, 'external_error_details', []);
	}

	private function normalize_external_result_identity(int $analysis_id, array $result, array $response, string $fallback_job_id = ''): array {
		$result['analysis_id'] = $analysis_id;
		$job_id = (string) ($response['job_id'] ?? $fallback_job_id);
		if ($job_id !== '') {
			$result['job_id'] = $job_id;
		}

		return $result;
	}

}
