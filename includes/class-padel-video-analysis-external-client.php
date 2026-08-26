<?php

if (!defined('ABSPATH')) {
	exit;
}

final class Padel_Video_Analysis_External_Client {
	private static ?self $instance = null;
	private const OPTION_SERVICE_URL = 'padel_video_analysis_service_url';
	private const OPTION_SERVICE_SECRET = 'padel_video_analysis_service_secret';

	public static function instance(): self {
		if (self::$instance === null) {
			self::$instance = new self();
		}

		return self::$instance;
	}

	private function __construct() {
	}

	public function is_enabled(): bool {
		return $this->get_service_url() !== '' && $this->get_service_secret() !== '';
	}

	public function get_service_url(): string {
		$url = $this->resolve_runtime_setting('PADEL_ANALYSIS_SERVICE_URL', 'PADEL_ANALYSIS_SERVICE_URL');
		if ($url !== '') {
			$this->persist_setting_if_missing(self::OPTION_SERVICE_URL, $url);
			return rtrim($url, '/');
		}

		$stored = (string) get_option(self::OPTION_SERVICE_URL, '');
		return rtrim($stored, '/');
	}

	public function get_service_secret(): string {
		$secret = $this->resolve_runtime_setting('PADEL_ANALYSIS_SERVICE_SECRET', 'PADEL_ANALYSIS_SERVICE_SECRET');
		if ($secret !== '') {
			$this->persist_setting_if_missing(self::OPTION_SERVICE_SECRET, $secret);
			return $secret;
		}

		return (string) get_option(self::OPTION_SERVICE_SECRET, '');
	}

	private function resolve_runtime_setting(string $env_key, string $constant_name): string {
		$candidates = [
			getenv($env_key),
			$_SERVER[$env_key] ?? null,
			$_ENV[$env_key] ?? null,
		];

		if (function_exists('apache_getenv')) {
			$candidates[] = apache_getenv($env_key);
		}

		if (defined($constant_name)) {
			$candidates[] = constant($constant_name);
		}

		foreach ($candidates as $candidate) {
			if (is_string($candidate) && trim($candidate) !== '') {
				return trim($candidate);
			}
		}

		return '';
	}

	private function persist_setting_if_missing(string $option_name, string $value): void {
		if ($value === '' || get_option($option_name, '') !== '') {
			return;
		}

		update_option($option_name, $value, false);
	}

	public function submit_job(int $analysis_id): array|WP_Error {
		$payload = Padel_Video_Analysis::instance()->build_external_job_payload($analysis_id);
		if (is_wp_error($payload)) {
			return $payload;
		}

		return $this->request_json('POST', '/api/v1/jobs', $payload);
	}

	public function select_match_player(string $job_id, string $selected_candidate_id, string $download_url = ''): array|WP_Error {
		$body = [
			'selected_player_candidate_id' => $selected_candidate_id,
		];
		if ($download_url !== '') {
			$body['video_download_url'] = $download_url;
		}

		return $this->request_json('POST', '/api/v1/jobs/' . rawurlencode($job_id) . '/selected-player', $body, true, 120);
	}

	public function update_contact_frame(string $job_id, int $frame_index): array|WP_Error {
		return $this->request_json('PATCH', '/api/v1/jobs/' . rawurlencode($job_id) . '/contact-frame', [
			'frame_index' => $frame_index,
		], true, 60);
	}

	public function fetch_artifact(int $job_id, string $artifact_name): array|WP_Error {
		return $this->request_binary('GET', '/api/v1/jobs/' . rawurlencode((string) $job_id) . '/artifacts/' . rawurlencode($artifact_name));
	}

	public function fetch_job(string $job_id): array|WP_Error {
		return $this->request_json('GET', '/api/v1/jobs/' . rawurlencode($job_id), [], true, 15);
	}

	public function delete_job(string $job_id): bool|WP_Error {
		$response = $this->request_json('DELETE', '/api/v1/jobs/' . rawurlencode($job_id), [], false);
		if (is_wp_error($response)) {
			return $response;
		}

		return true;
	}

	public function request_json(string $method, string $path, array $body = [], bool $expect_json = true, ?int $timeout_seconds = null): array|WP_Error {
		$response = $this->request_response($method, $path, $body, $timeout_seconds);
		if (is_wp_error($response)) {
			return $response;
		}

		$raw_body = (string) ($response['body'] ?? '');
		$decoded = $raw_body !== '' ? json_decode($raw_body, true) : [];
		$status = (int) ($response['status_code'] ?? 500);
		if ($status >= 200 && $status < 300) {
			if (!$expect_json) {
				return is_array($decoded) ? $decoded : [];
			}
			if (is_array($decoded)) {
				return $decoded;
			}
		}

		$detail = [
			'status_code' => $status,
			'body' => is_array($decoded) ? $decoded : $raw_body,
		];

		if ($status >= 500) {
			return new WP_Error(
				'padel_external_service_retryable',
				'Temporary external service error.',
				$detail
			);
		}

		return new WP_Error(
			'padel_external_service_failed',
			'External analysis service request failed.',
			$detail
		);
	}

	public function request_binary(string $method, string $path, array $body = [], ?int $timeout_seconds = null): array|WP_Error {
		$response = $this->request_response($method, $path, $body, $timeout_seconds);
		if (is_wp_error($response)) {
			return $response;
		}

		return $response;
	}

	public function request_response(string $method, string $path, array $body = [], ?int $timeout_seconds = null): array|WP_Error {
		$service_url = $this->get_service_url();
		$secret = $this->get_service_secret();
		if ($service_url === '' || $secret === '') {
			return new WP_Error('padel_external_service_disabled', 'External analysis service is not configured.', ['status' => 500]);
		}

		$url = $service_url . $path;
		$headers = [
			'Accept' => 'application/json',
			'Content-Type' => 'application/json',
			'X-Padel-API-Secret' => $secret,
		];

		$args = [
			'method' => strtoupper($method),
			'headers' => $headers,
			'timeout' => $timeout_seconds ?? 12,
		];

		if (!empty($body)) {
			$args['body'] = wp_json_encode($body, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
		}

		$attempts = 3;
		$last_error = null;
		for ($attempt = 1; $attempt <= $attempts; $attempt++) {
			$response = wp_remote_request($url, $args);
			if (is_wp_error($response)) {
				$last_error = $response;
				sleep($attempt);
				continue;
			}

			$status = (int) wp_remote_retrieve_response_code($response);
			$raw_body = (string) wp_remote_retrieve_body($response);
			$decoded = $raw_body !== '' ? json_decode($raw_body, true) : [];
			if ($status >= 500 && $attempt < $attempts) {
				sleep($attempt);
				$last_error = new WP_Error('padel_external_service_retryable', 'Temporary external service error.', [
					'status_code' => $status,
					'body' => is_array($decoded) ? $decoded : $raw_body,
				]);
				continue;
			}

			return [
				'status_code' => $status,
				'body' => $raw_body,
				'decoded' => is_array($decoded) ? $decoded : null,
				'headers' => wp_remote_retrieve_headers($response),
			];
		}

		if ($last_error instanceof WP_Error) {
			return $last_error;
		}

		return new WP_Error('padel_external_service_failed', 'External analysis service request failed.', [
			'status_code' => 500,
		]);
	}

	public function build_job_payload(int $analysis_id): array|WP_Error {
		$analysis = get_post($analysis_id);
		if (!$analysis instanceof WP_Post || $analysis->post_type !== Padel_Video_Analysis::CPT) {
			return new WP_Error('padel_invalid_analysis', 'Analysis record not found.', ['status' => 404]);
		}

		$analysis_mode = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'analysis_mode', 'stroke');
		$video_path = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_path', '');
		$video_sha256 = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_sha256', '');
		$video_size = (int) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_size', 0);
		$video_duration = (float) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_duration_seconds', 0);

		if ($video_path === '' || !file_exists($video_path)) {
			return new WP_Error('padel_missing_video', 'Video file is missing.', ['status' => 404]);
		}

		$payload = [
			'analysis_id' => $analysis_id,
			'owner_user_id' => (int) $analysis->post_author,
			'analysis_mode' => $analysis_mode === 'match' ? 'match' : 'stroke',
			'shot_type' => (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'shot_type', 'forehand'),
			'dominant_hand' => (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'dominant_hand', 'right'),
			'camera_angle' => (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'camera_angle', 'baseline'),
			'video_name' => (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_name', ''),
			'video_mime_type' => (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_mime_type', ''),
			'video_size' => $video_size,
			'video_duration_seconds' => $video_duration,
			'video_sha256' => $video_sha256,
			'submission_fingerprint' => Padel_Video_Analysis::instance()->build_submission_fingerprint(
				(int) $analysis->post_author,
				$analysis_mode === 'match' ? 'match' : 'stroke',
				(string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'shot_type', 'forehand'),
				(string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'dominant_hand', 'right'),
				(string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'camera_angle', 'baseline'),
				$video_sha256
			),
		];

		$download_url = Padel_Video_Analysis::instance()->build_private_video_download_url($analysis_id);
		if (is_wp_error($download_url)) {
			return $download_url;
		}

		$payload['video_download_url'] = $download_url;

		return $payload;
	}
}
