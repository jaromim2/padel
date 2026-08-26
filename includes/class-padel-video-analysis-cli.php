<?php

if (!defined('ABSPATH')) {
	exit;
}

final class Padel_Video_Analysis_CLI {
	private static ?self $instance = null;

	public static function instance(): self {
		if (self::$instance === null) {
			self::$instance = new self();
		}

		return self::$instance;
	}

	private function __construct() {
	}

	public function backfill_feedback(array $args, array $assoc_args): void {
		if (!Padel_Video_Analysis::instance()->can_current_user_review() && !current_user_can('manage_options')) {
			WP_CLI::error('Administrator access is required.');
		}

		$dry_run = $this->normalize_bool($assoc_args['dry-run'] ?? false);
		$force = $this->normalize_bool($assoc_args['force'] ?? false);
		$analysis_ids = $this->parse_analysis_ids((string) ($assoc_args['analysis-ids'] ?? ''));
		$from_id = absint($assoc_args['from-id'] ?? 0);
		$to_id = absint($assoc_args['to-id'] ?? 0);

		$candidates = $this->collect_candidates($analysis_ids, $from_id, $to_id);
		$request = [
			'dry_run' => $dry_run,
			'force' => $force,
			'analyses' => array_map(
				fn(WP_Post $post) => $this->build_analysis_payload($post),
				$candidates
			),
		];

		$response = $this->run_python_helper($request);
		if (is_wp_error($response)) {
			WP_CLI::error($response->get_error_message());
		}

		if (!$dry_run) {
			$this->apply_updates((array) ($response['results'] ?? []));
		}

		$queue = $this->augment_queue_review_state((array) ($response['queue'] ?? []));
		$report = [
			'command' => 'backfill-feedback',
			'dry_run' => $dry_run,
			'force' => $force,
			'filters' => [
				'analysis_ids' => $analysis_ids,
				'from_id' => $from_id ?: null,
				'to_id' => $to_id ?: null,
			],
			'summary' => $response['summary'] ?? [],
			'results' => $response['results'] ?? [],
			'review_queue' => $queue,
		];

		WP_CLI::line(wp_json_encode($report, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
	}

	private function normalize_bool(mixed $value): bool {
		return filter_var($value, FILTER_VALIDATE_BOOL, FILTER_NULL_ON_FAILURE) ?? false;
	}

	private function parse_analysis_ids(string $value): array {
		$ids = [];
		foreach (array_filter(array_map('trim', explode(',', $value))) as $item) {
			if (is_numeric($item) && (int) $item > 0) {
				$ids[] = (int) $item;
			}
		}
		return array_values(array_unique($ids));
	}

	private function collect_candidates(array $analysis_ids, int $from_id, int $to_id): array {
		$posts = get_posts([
			'post_type' => Padel_Video_Analysis::CPT,
			'post_status' => 'any',
			'numberposts' => -1,
			'orderby' => 'ID',
			'order' => 'ASC',
		]);

		if (!is_array($posts)) {
			return [];
		}

		$id_map = $analysis_ids !== [] ? array_fill_keys($analysis_ids, true) : [];
		$filtered = [];
		foreach ($posts as $post) {
			if (!$post instanceof WP_Post) {
				continue;
			}

			$analysis_id = (int) $post->ID;
			$status = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'status', '');
			if ($status !== Padel_Video_Analysis::STATUS_COMPLETED) {
				continue;
			}

			$analysis_mode = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'analysis_mode', 'stroke');
			if ($analysis_mode !== 'stroke') {
				continue;
			}

			if ($analysis_ids !== [] && !isset($id_map[$analysis_id])) {
				continue;
			}

			if ($from_id > 0 && $analysis_id < $from_id) {
				continue;
			}

			if ($to_id > 0 && $analysis_id > $to_id) {
				continue;
			}

			$filtered[] = $post;
		}

		return array_values($filtered);
	}

	private function build_analysis_payload(WP_Post $post): array {
		$analysis_id = (int) $post->ID;
		$result = Padel_Video_Analysis::instance()->get_meta($analysis_id, 'analysis_result', []);
		if (!is_array($result) || $result === []) {
			$result = Padel_Video_Analysis::instance()->get_meta($analysis_id, 'result_payload', []);
		}
		if (!is_array($result)) {
			$result = [];
		}

		return [
			'analysis_id' => $analysis_id,
			'status' => (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'status', ''),
			'analysis_mode' => (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'analysis_mode', 'stroke'),
			'source' => [
				'shot_type' => (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'shot_type', 'forehand'),
				'dominant_hand' => (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'dominant_hand', 'right'),
				'camera_angle' => (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'camera_angle', 'baseline'),
				'analysis_mode' => (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'analysis_mode', 'stroke'),
			],
			'result' => $result,
		];
	}

	private function run_python_helper(array $payload): array|WP_Error {
		$compose_file = PADEL_VIDEO_ANALYSIS_PATH . 'padel-analysis-service/docker-compose.yml';
		if (!file_exists($compose_file)) {
			return new WP_Error('padel_backfill_missing_compose', 'Analysis service compose file not found.', ['status' => 500]);
		}

		$docker = trim((string) shell_exec('command -v docker 2>/dev/null'));
		if ($docker === '') {
			return new WP_Error('padel_backfill_missing_docker', 'Docker is required for backfill.', ['status' => 500]);
		}

		$build_command = sprintf(
			'docker compose -f %s build --quiet padel-analysis-service',
			escapeshellarg($compose_file)
		);
		$build_result = $this->run_process($build_command);
		if (is_wp_error($build_result)) {
			return $build_result;
		}

		$command = sprintf(
			'docker compose -f %s run --rm -T padel-analysis-service python -m app.backfill_feedback --input - --output -',
			escapeshellarg($compose_file)
		);
		return $this->run_json_command($command, $payload);
	}

	private function run_process(string $command): array|WP_Error {
		$descriptors = [
			0 => ['pipe', 'r'],
			1 => ['pipe', 'w'],
			2 => ['pipe', 'w'],
		];
		$process = proc_open($command, $descriptors, $pipes, PADEL_VIDEO_ANALYSIS_PATH . 'padel-analysis-service');
		if (!is_resource($process)) {
			return new WP_Error('padel_backfill_process_failed', 'Unable to launch the backfill helper.', ['status' => 500]);
		}

		fclose($pipes[0]);
		$stdout = stream_get_contents($pipes[1]);
		$stderr = stream_get_contents($pipes[2]);
		fclose($pipes[1]);
		fclose($pipes[2]);
		$exit_code = proc_close($process);
		if ($exit_code !== 0) {
			return new WP_Error('padel_backfill_failed', trim($stderr) !== '' ? trim($stderr) : 'Backfill helper failed.', ['status' => 500, 'stdout' => $stdout, 'stderr' => $stderr]);
		}

		return [
			'stdout' => $stdout,
			'stderr' => $stderr,
		];
	}

	private function run_json_command(string $command, array $payload): array|WP_Error {
		$descriptors = [
			0 => ['pipe', 'r'],
			1 => ['pipe', 'w'],
			2 => ['pipe', 'w'],
		];
		$process = proc_open($command, $descriptors, $pipes, PADEL_VIDEO_ANALYSIS_PATH . 'padel-analysis-service');
		if (!is_resource($process)) {
			return new WP_Error('padel_backfill_process_failed', 'Unable to launch the backfill helper.', ['status' => 500]);
		}

		fwrite($pipes[0], wp_json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
		fclose($pipes[0]);
		$stdout = stream_get_contents($pipes[1]);
		$stderr = stream_get_contents($pipes[2]);
		fclose($pipes[1]);
		fclose($pipes[2]);
		$exit_code = proc_close($process);

		$response = json_decode((string) $stdout, true);
		if (!is_array($response)) {
			return new WP_Error('padel_backfill_invalid_response', 'Backfill helper returned an invalid response.', ['status' => 500, 'stderr' => $stderr, 'stdout' => $stdout]);
		}

		if ($exit_code !== 0 || isset($response['error'])) {
			$message = (string) ($response['error']['message'] ?? trim($stderr) ?: 'Backfill helper failed.');
			return new WP_Error(
				(string) ($response['error']['type'] ?? 'padel_backfill_failed'),
				$message,
				['status' => 500, 'stderr' => $stderr, 'response' => $response]
			);
		}

		return $response;
	}

	private function apply_updates(array $results): void {
		foreach ($results as $item) {
			if (!is_array($item) || (string) ($item['status'] ?? '') !== 'updated') {
				continue;
			}

			$analysis_id = absint($item['analysis_id'] ?? 0);
			$technical_feedback = $item['technical_feedback'] ?? null;
			if ($analysis_id <= 0 || !is_array($technical_feedback)) {
				continue;
			}

			$result = Padel_Video_Analysis::instance()->get_meta($analysis_id, 'analysis_result', []);
			if (!is_array($result) || $result === []) {
				$result = Padel_Video_Analysis::instance()->get_meta($analysis_id, 'result_payload', []);
			}
			if (!is_array($result)) {
				$result = [];
			}

			$result['technical_feedback'] = $technical_feedback;
			Padel_Video_Analysis::instance()->set_meta($analysis_id, 'analysis_result', $result);
			Padel_Video_Analysis::instance()->set_meta($analysis_id, 'technical_feedback_rules_version', (string) ($technical_feedback['rules_version'] ?? ''));
			Padel_Video_Analysis::instance()->set_meta($analysis_id, 'technical_feedback_calculated_at', (string) ($technical_feedback['calculated_at'] ?? ''));
			Padel_Video_Analysis::instance()->set_meta($analysis_id, 'technical_feedback_generation_mode', (string) ($technical_feedback['generation_mode'] ?? ''));
			Padel_Video_Analysis::instance()->set_meta($analysis_id, 'technical_feedback_source_analysis_id', absint($technical_feedback['source_analysis_id'] ?? $analysis_id));
			Padel_Video_Analysis::instance()->set_meta($analysis_id, 'technical_feedback_legacy_derived', !empty($technical_feedback['legacy_derived']));
		}
	}

	private function augment_queue_review_state(array $queue): array {
		foreach ($queue as &$item) {
			if (!is_array($item)) {
				continue;
			}

			$analysis_id = absint($item['analysis_id'] ?? 0);
			if ($analysis_id <= 0) {
				continue;
			}

			$item['coach_review_exists'] = count(Padel_Video_Analysis_Review_Store::instance()->get_review_versions($analysis_id)) > 0;
		}
		unset($item);

		return $queue;
	}

	private function extract_capabilities(array $result): array {
		$stroke = $result['stroke'] ?? [];
		if (!is_array($stroke)) {
			$stroke = [];
		}
		$capabilities = $stroke['analysis_capabilities'] ?? [];
		if (!is_array($capabilities)) {
			$capabilities = [];
		}

		$knee_angles = $capabilities['knee_angles'] ?? [];
		if (!is_array($knee_angles)) {
			$knee_angles = [];
		}

		return [
			'pose_confidence' => (string) ($result['pose']['confidence_level'] ?? 'insufficient'),
			'stroke_confidence' => (string) ($stroke['confidence_level'] ?? 'insufficient'),
			'stroke_phases' => (string) ($capabilities['stroke_phases']['confidence_level'] ?? 'insufficient'),
			'elbow_angles' => (string) ($capabilities['elbow_angles']['confidence_level'] ?? 'insufficient'),
			'shoulder_rotation' => (string) ($capabilities['shoulder_rotation']['confidence_level'] ?? 'insufficient'),
			'hip_rotation' => (string) ($capabilities['hip_rotation']['confidence_level'] ?? 'insufficient'),
			'knee_left' => (string) ($knee_angles['left']['confidence_level'] ?? 'insufficient'),
			'knee_right' => (string) ($knee_angles['right']['confidence_level'] ?? 'insufficient'),
			'balance' => (string) ($capabilities['balance']['confidence_level'] ?? 'insufficient'),
			'recovery' => (string) ($capabilities['recovery']['confidence_level'] ?? 'insufficient'),
			'legacy_derived' => !empty($result['technical_feedback']['legacy_derived']),
		];
	}

	private function derive_video_suitability(array $result): string {
		$pose_confidence = (string) ($result['pose']['confidence_level'] ?? 'insufficient');
		$stroke_confidence = (string) ($result['stroke']['confidence_level'] ?? 'insufficient');
		if ($pose_confidence === 'high' && in_array($stroke_confidence, ['high', 'medium'], true)) {
			return 'usable';
		}
		if (in_array($pose_confidence, ['medium', 'low'], true) || in_array($stroke_confidence, ['low', 'insufficient'], true)) {
			return 'limited';
		}
		return 'unusable';
	}

	private function queue_category(array $result): string {
		$technical_feedback = $result['technical_feedback'] ?? [];
		if (!is_array($technical_feedback)) {
			$technical_feedback = [];
		}
		$pose_confidence = (string) ($result['pose']['confidence_level'] ?? 'insufficient');
		$stroke = $result['stroke'] ?? [];
		if (!is_array($stroke)) {
			$stroke = [];
		}
		$capabilities = $stroke['analysis_capabilities'] ?? [];
		if (!is_array($capabilities)) {
			$capabilities = [];
		}
		$knee_left = $capabilities['knee_angles']['left'] ?? [];
		$knee_right = $capabilities['knee_angles']['right'] ?? [];

		$findings_count = count((array) ($technical_feedback['findings'] ?? []));
		$possible_count = count((array) ($technical_feedback['possible_observations'] ?? []));
		$neutral_count = count((array) ($technical_feedback['neutral_measurements'] ?? []));
		$withheld_count = count((array) ($technical_feedback['withheld_findings'] ?? []));

		if ($withheld_count > 0 && ($findings_count + $possible_count + $neutral_count) === 0) {
			return 'mostly_withheld';
		}
		if (!empty($knee_left['available']) xor !empty($knee_right['available'])) {
			return 'one_knee_only';
		}
		if ($pose_confidence === 'high' && (string) ($stroke['confidence_level'] ?? 'insufficient') === 'high' && empty($technical_feedback['legacy_derived'])) {
			return 'high_full_body';
		}
		if (in_array($pose_confidence, ['low', 'insufficient'], true) || in_array((string) ($stroke['confidence_level'] ?? 'insufficient'), ['low', 'insufficient'], true)) {
			return 'low_confidence';
		}
		foreach (['stroke_phases', 'elbow_angles', 'shoulder_rotation', 'hip_rotation', 'balance', 'recovery'] as $name) {
			if (in_array((string) ($capabilities[$name]['confidence_level'] ?? 'insufficient'), ['medium', 'low'], true)) {
				return 'partial_capability';
			}
		}
		foreach (['left', 'right'] as $side) {
			if (in_array((string) ($capabilities['knee_angles'][$side]['confidence_level'] ?? 'insufficient'), ['medium', 'low'], true)) {
				return 'partial_capability';
			}
		}
		return 'balanced';
	}
}
