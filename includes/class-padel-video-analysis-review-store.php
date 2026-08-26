<?php

if (!defined('ABSPATH')) {
	exit;
}

final class Padel_Video_Analysis_Review_Store {
	private const SCHEMA_VERSION = '1';
	private static ?self $instance = null;

	public static function instance(): self {
		if (self::$instance === null) {
			self::$instance = new self();
		}

		return self::$instance;
	}

	private function __construct() {
		$this->maybe_install_tables();
	}

	public function activate(): void {
		$this->maybe_install_tables(true);
	}

	public function maybe_install_tables(bool $force = false): void {
		$installed_version = (string) get_option('padel_video_analysis_review_schema_version', '');
		if (!$force && $installed_version === self::SCHEMA_VERSION) {
			return;
		}

		global $wpdb;

		require_once ABSPATH . 'wp-admin/includes/upgrade.php';

		$charset_collate = $wpdb->get_charset_collate();
		$reviews = $this->reviews_table_name();
		$items = $this->review_items_table_name();

		$sql = [];
		$sql[] = "CREATE TABLE {$reviews} (
			id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
			analysis_id BIGINT(20) UNSIGNED NOT NULL,
			rules_version VARCHAR(80) NOT NULL DEFAULT '',
			automatic_result_checksum CHAR(64) NOT NULL DEFAULT '',
			automatic_result_snapshot_json LONGTEXT NULL,
			reviewer_user_id BIGINT(20) UNSIGNED NOT NULL DEFAULT 0,
			reviewed_at DATETIME NOT NULL,
			updated_at DATETIME NOT NULL,
			pose_confidence VARCHAR(20) NOT NULL DEFAULT '',
			stroke_confidence VARCHAR(20) NOT NULL DEFAULT '',
			camera_angle VARCHAR(32) NOT NULL DEFAULT '',
			full_body_visible TINYINT(1) NOT NULL DEFAULT 0,
			dominant_hand VARCHAR(20) NOT NULL DEFAULT '',
			overall_video_suitability VARCHAR(20) NOT NULL DEFAULT '',
			overall_analysis_usefulness VARCHAR(20) NOT NULL DEFAULT '',
			item_total_count INT(11) UNSIGNED NOT NULL DEFAULT 0,
			finding_count INT(11) UNSIGNED NOT NULL DEFAULT 0,
			possible_count INT(11) UNSIGNED NOT NULL DEFAULT 0,
			neutral_count INT(11) UNSIGNED NOT NULL DEFAULT 0,
			withheld_count INT(11) UNSIGNED NOT NULL DEFAULT 0,
			manual_missed_count INT(11) UNSIGNED NOT NULL DEFAULT 0,
			PRIMARY KEY  (id),
			UNIQUE KEY analysis_rules_version (analysis_id, rules_version),
			KEY analysis_id (analysis_id),
			KEY rules_version (rules_version),
			KEY reviewer_user_id (reviewer_user_id),
			KEY reviewed_at (reviewed_at)
		) {$charset_collate};";
		$sql[] = "CREATE TABLE {$items} (
			id BIGINT(20) UNSIGNED NOT NULL AUTO_INCREMENT,
			review_id BIGINT(20) UNSIGNED NOT NULL,
			item_kind VARCHAR(32) NOT NULL DEFAULT '',
			rule_id VARCHAR(191) NOT NULL DEFAULT '',
			title VARCHAR(255) NOT NULL DEFAULT '',
			review_label VARCHAR(32) NOT NULL DEFAULT '',
			severity VARCHAR(20) NOT NULL DEFAULT '',
			reviewer_note LONGTEXT NULL,
			timestamp_correct TINYINT(1) NULL,
			wording_correct TINYINT(1) NULL,
			should_have_been_withheld TINYINT(1) NULL,
			measurement_reasonable TINYINT(1) NULL,
			manual_miss_kind VARCHAR(32) NOT NULL DEFAULT '',
			manual_rule_id VARCHAR(191) NOT NULL DEFAULT '',
			confidence_level VARCHAR(20) NOT NULL DEFAULT '',
			supporting_timestamp_ms BIGINT(20) UNSIGNED NULL,
			supporting_frame_index BIGINT(20) UNSIGNED NULL,
			item_snapshot_json LONGTEXT NULL,
			created_at DATETIME NOT NULL,
			updated_at DATETIME NOT NULL,
			PRIMARY KEY  (id),
			KEY review_id (review_id),
			KEY item_kind (item_kind),
			KEY rule_id (rule_id),
			KEY manual_rule_id (manual_rule_id),
			KEY review_label (review_label)
		) {$charset_collate};";

		foreach ($sql as $statement) {
			dbDelta($statement);
		}

		update_option('padel_video_analysis_review_schema_version', self::SCHEMA_VERSION, false);
	}

	public function reviews_table_name(): string {
		global $wpdb;
		return $wpdb->prefix . 'padel_video_analysis_reviews';
	}

	public function review_items_table_name(): string {
		global $wpdb;
		return $wpdb->prefix . 'padel_video_analysis_review_items';
	}

	public function delete_reviews_for_analysis(int $analysis_id): void {
		global $wpdb;

		$review_ids = $wpdb->get_col(
			$wpdb->prepare(
				"SELECT id FROM {$this->reviews_table_name()} WHERE analysis_id = %d",
				$analysis_id
			)
		);

		if (is_array($review_ids) && $review_ids !== []) {
			$review_ids = array_map('absint', $review_ids);
			$placeholders = implode(',', array_fill(0, count($review_ids), '%d'));
			$wpdb->query(
				$wpdb->prepare(
					"DELETE FROM {$this->review_items_table_name()} WHERE review_id IN ({$placeholders})",
					$review_ids
				)
			);
		}

		$wpdb->delete($this->reviews_table_name(), ['analysis_id' => $analysis_id], ['%d']);
	}

	public function upsert_review(
		int $analysis_id,
		string $rules_version,
		string $automatic_result_checksum,
		int $reviewer_user_id,
		array $review_header,
		array $items
	): array|WP_Error {
		global $wpdb;

		$existing = $this->get_review_row($analysis_id, $rules_version);
		$now = gmdate('Y-m-d H:i:s');
		$header_data = [
			'analysis_id' => $analysis_id,
			'rules_version' => $rules_version,
			'automatic_result_checksum' => $automatic_result_checksum,
			'automatic_result_snapshot_json' => wp_json_encode($review_header['automatic_result_snapshot'] ?? [], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
			'reviewer_user_id' => $reviewer_user_id,
			'reviewed_at' => $now,
			'updated_at' => $now,
			'pose_confidence' => (string) ($review_header['pose_confidence'] ?? ''),
			'stroke_confidence' => (string) ($review_header['stroke_confidence'] ?? ''),
			'camera_angle' => (string) ($review_header['camera_angle'] ?? ''),
			'full_body_visible' => !empty($review_header['full_body_visible']) ? 1 : 0,
			'dominant_hand' => (string) ($review_header['dominant_hand'] ?? ''),
			'overall_video_suitability' => (string) ($review_header['overall_video_suitability'] ?? ''),
			'overall_analysis_usefulness' => (string) ($review_header['overall_analysis_usefulness'] ?? ''),
			'item_total_count' => count($items),
			'finding_count' => 0,
			'possible_count' => 0,
			'neutral_count' => 0,
			'withheld_count' => 0,
			'manual_missed_count' => 0,
		];

		if (is_array($existing)) {
			$wpdb->update($this->reviews_table_name(), $header_data, ['id' => (int) $existing['id']], array_fill(0, count($header_data), '%s'), ['%d']);
			$review_id = (int) $existing['id'];
			$wpdb->delete($this->review_items_table_name(), ['review_id' => $review_id], ['%d']);
		} else {
			$wpdb->insert(
				$this->reviews_table_name(),
				$header_data,
				['%d', '%s', '%s', '%s', '%d', '%s', '%s', '%s', '%s', '%s', '%d', '%s', '%s', '%s', '%d', '%d', '%d', '%d', '%d', '%d']
			);
			$review_id = (int) $wpdb->insert_id;
		}

		if ($review_id <= 0) {
			return new WP_Error('padel_review_save_failed', 'Unable to save the review.', ['status' => 500]);
		}

		$counts = [
			'finding_count' => 0,
			'possible_count' => 0,
			'neutral_count' => 0,
			'withheld_count' => 0,
			'manual_missed_count' => 0,
		];

		foreach ($items as $item) {
			$item_kind = (string) ($item['item_kind'] ?? '');
			if ($item_kind === '') {
				continue;
			}

			$automatic_item = $item_kind !== 'manual_missed_finding';
			if ($automatic_item) {
				if ($item_kind === 'finding') {
					$counts['finding_count']++;
				} elseif ($item_kind === 'possible_observation') {
					$counts['possible_count']++;
				} elseif ($item_kind === 'neutral_measurement') {
					$counts['neutral_count']++;
				} elseif ($item_kind === 'withheld_finding') {
					$counts['withheld_count']++;
				}
			} else {
				$counts['manual_missed_count']++;
			}

			$inserted = $wpdb->insert(
				$this->review_items_table_name(),
				[
					'review_id' => $review_id,
					'item_kind' => $item_kind,
					'rule_id' => (string) ($item['rule_id'] ?? ''),
					'title' => (string) ($item['title'] ?? ''),
					'review_label' => (string) ($item['review_label'] ?? ''),
					'severity' => (string) ($item['severity'] ?? ''),
					'reviewer_note' => (string) ($item['reviewer_note'] ?? ''),
					'timestamp_correct' => $this->bool_to_nullable_int($item['timestamp_correct'] ?? null),
					'wording_correct' => $this->bool_to_nullable_int($item['wording_correct'] ?? null),
					'should_have_been_withheld' => $this->bool_to_nullable_int($item['should_have_been_withheld'] ?? null),
					'measurement_reasonable' => $this->bool_to_nullable_int($item['measurement_reasonable'] ?? null),
					'manual_miss_kind' => (string) ($item['manual_miss_kind'] ?? ''),
					'manual_rule_id' => (string) ($item['manual_rule_id'] ?? ''),
					'confidence_level' => (string) ($item['confidence_level'] ?? ''),
					'supporting_timestamp_ms' => isset($item['supporting_timestamp_ms']) && is_numeric($item['supporting_timestamp_ms']) ? (int) $item['supporting_timestamp_ms'] : null,
					'supporting_frame_index' => isset($item['supporting_frame_index']) && is_numeric($item['supporting_frame_index']) ? (int) $item['supporting_frame_index'] : null,
					'item_snapshot_json' => wp_json_encode($item, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
					'created_at' => $now,
					'updated_at' => $now,
				],
				['%d', '%s', '%s', '%s', '%s', '%s', '%s', '%d', '%d', '%d', '%d', '%s', '%s', '%s', '%d', '%d', '%s', '%s', '%s']
			);

			if ($inserted === false) {
				return new WP_Error('padel_review_save_failed', 'Unable to save the review items.', ['status' => 500]);
			}
		}

		$wpdb->update(
			$this->reviews_table_name(),
			$counts,
			['id' => $review_id],
			['%d', '%d', '%d', '%d', '%d'],
			['%d']
		);

		return $this->get_review($analysis_id, $rules_version);
	}

	public function get_review(int $analysis_id, ?string $rules_version = null): array {
		$row = $this->get_review_row($analysis_id, $rules_version);
		if (!is_array($row)) {
			return [];
		}

		$items = $this->get_review_items((int) $row['id']);
		$row['automatic_result_snapshot'] = $this->decode_json((string) ($row['automatic_result_snapshot_json'] ?? ''));
		unset($row['automatic_result_snapshot_json']);
		$row['items'] = $items;
		$row['item_counts'] = [
			'finding_count' => (int) ($row['finding_count'] ?? 0),
			'possible_count' => (int) ($row['possible_count'] ?? 0),
			'neutral_count' => (int) ($row['neutral_count'] ?? 0),
			'withheld_count' => (int) ($row['withheld_count'] ?? 0),
			'manual_missed_count' => (int) ($row['manual_missed_count'] ?? 0),
		];

		return $row;
	}

	public function get_review_versions(int $analysis_id): array {
		global $wpdb;
		$rows = $wpdb->get_results(
			$wpdb->prepare(
				"SELECT rules_version, reviewed_at FROM {$this->reviews_table_name()} WHERE analysis_id = %d ORDER BY reviewed_at DESC, id DESC",
				$analysis_id
			),
			ARRAY_A
		);

		return is_array($rows) ? array_values($rows) : [];
	}

	public function get_review_rows_by_rules_version(?string $rules_version = null, int $limit = 10): array {
		global $wpdb;
		$query = "SELECT * FROM {$this->reviews_table_name()}";
		$params = [];
		if (is_string($rules_version) && $rules_version !== '') {
			$query .= ' WHERE rules_version = %s';
			$params[] = $rules_version;
		}
		$query .= ' ORDER BY reviewed_at DESC, id DESC';
		if ($limit > 0) {
			$query .= ' LIMIT %d';
			$params[] = $limit;
		}

		$prepared = $params !== [] ? $wpdb->prepare($query, $params) : $query;
		$rows = $wpdb->get_results($prepared, ARRAY_A);
		return is_array($rows) ? $rows : [];
	}

	public function get_calibration_summary(?string $rules_version = null): array {
		global $wpdb;
		$reviews = $this->reviews_table_name();
		$items = $this->review_items_table_name();

		$where = "WHERE i.item_kind IN ('finding', 'possible_observation', 'neutral_measurement', 'withheld_finding')";
		$params = [];
		if (is_string($rules_version) && $rules_version !== '') {
			$where .= ' AND r.rules_version = %s';
			$params[] = $rules_version;
		}

		$prepared = $params !== [] ? $wpdb->prepare($where, $params) : $where;
		$rows = $wpdb->get_results(
			"SELECT
				i.rule_id,
				COUNT(*) AS evaluated_count,
				SUM(CASE WHEN (
					(i.item_kind IN ('finding', 'possible_observation') AND i.review_label = 'correct')
					OR (i.item_kind = 'neutral_measurement' AND i.measurement_reasonable = 1)
					OR (i.item_kind = 'withheld_finding' AND i.review_label = 'correct')
				) THEN 1 ELSE 0 END) AS correct_count,
				SUM(CASE WHEN i.review_label = 'partially_correct' THEN 1 ELSE 0 END) AS partially_correct_count,
				SUM(CASE WHEN (
					(i.item_kind IN ('finding', 'possible_observation') AND i.review_label = 'incorrect')
					OR (i.item_kind = 'neutral_measurement' AND i.measurement_reasonable = 0 AND i.review_label = 'incorrect')
					OR (i.item_kind = 'withheld_finding' AND i.review_label = 'should_not_have_been_withheld')
				) THEN 1 ELSE 0 END) AS incorrect_count,
				SUM(CASE WHEN (
					(i.item_kind IN ('finding', 'possible_observation') AND i.review_label = 'unclear')
					OR (i.item_kind = 'neutral_measurement' AND i.review_label = 'cannot_verify')
					OR (i.item_kind = 'withheld_finding' AND i.review_label = 'unclear')
				) THEN 1 ELSE 0 END) AS unclear_count,
				AVG(CASE i.confidence_level WHEN 'high' THEN 1.0 WHEN 'medium' THEN 0.75 WHEN 'low' THEN 0.45 ELSE 0.0 END) AS average_confidence
			FROM {$items} i
			INNER JOIN {$reviews} r ON r.id = i.review_id
			{$prepared}
			GROUP BY i.rule_id
			ORDER BY evaluated_count DESC, i.rule_id ASC",
			ARRAY_A
		);

		$grouped = [
			'pose_confidence' => $this->group_summary($rules_version, 'pose_confidence'),
			'stroke_confidence' => $this->group_summary($rules_version, 'stroke_confidence'),
			'camera_angle' => $this->group_summary($rules_version, 'camera_angle'),
			'full_body_visible' => $this->group_summary($rules_version, 'full_body_visible'),
			'dominant_hand' => $this->group_summary($rules_version, 'dominant_hand'),
		];

		return [
			'rules_version' => $rules_version,
			'rule_rows' => is_array($rows) ? $rows : [],
			'grouped' => $grouped,
		];
	}

	public function get_review_queue(int $limit = 10, ?string $rules_version = null): array {
		return array_map(
			static fn(array $row) => $row,
			$this->get_review_rows_by_rules_version($rules_version, $limit)
		);
	}

	public function get_latest_review_rules_version(int $analysis_id): string {
		$versions = $this->get_review_versions($analysis_id);
		return (string) ($versions[0]['rules_version'] ?? '');
	}

	private function get_review_row(int $analysis_id, ?string $rules_version = null): ?array {
		global $wpdb;

		if (is_string($rules_version) && $rules_version !== '') {
			$row = $wpdb->get_row(
				$wpdb->prepare(
					"SELECT * FROM {$this->reviews_table_name()} WHERE analysis_id = %d AND rules_version = %s ORDER BY reviewed_at DESC, id DESC LIMIT 1",
					$analysis_id,
					$rules_version
				),
				ARRAY_A
			);
			return is_array($row) ? $row : null;
		}

		$row = $wpdb->get_row(
			$wpdb->prepare(
				"SELECT * FROM {$this->reviews_table_name()} WHERE analysis_id = %d ORDER BY reviewed_at DESC, id DESC LIMIT 1",
				$analysis_id
			),
			ARRAY_A
		);

		return is_array($row) ? $row : null;
	}

	private function get_review_items(int $review_id): array {
		global $wpdb;
		$rows = $wpdb->get_results(
			$wpdb->prepare(
				"SELECT * FROM {$this->review_items_table_name()} WHERE review_id = %d ORDER BY id ASC",
				$review_id
			),
			ARRAY_A
		);

		if (!is_array($rows)) {
			return [];
		}

		return array_map(function (array $row): array {
			$row['timestamp_correct'] = isset($row['timestamp_correct']) ? (bool) $row['timestamp_correct'] : null;
			$row['wording_correct'] = isset($row['wording_correct']) ? (bool) $row['wording_correct'] : null;
			$row['should_have_been_withheld'] = isset($row['should_have_been_withheld']) ? (bool) $row['should_have_been_withheld'] : null;
			$row['measurement_reasonable'] = isset($row['measurement_reasonable']) ? (bool) $row['measurement_reasonable'] : null;
			$row['supporting_timestamp_ms'] = isset($row['supporting_timestamp_ms']) ? (int) $row['supporting_timestamp_ms'] : null;
			$row['supporting_frame_index'] = isset($row['supporting_frame_index']) ? (int) $row['supporting_frame_index'] : null;
			$row['snapshot'] = $this->decode_json((string) ($row['item_snapshot_json'] ?? ''));
			unset($row['item_snapshot_json']);
			return $row;
		}, $rows);
	}

	private function group_summary(?string $rules_version, string $field): array {
		global $wpdb;

		$reviews = $this->reviews_table_name();
		$items = $this->review_items_table_name();
		$where = "WHERE i.item_kind IN ('finding', 'possible_observation', 'neutral_measurement', 'withheld_finding')";
		$params = [];
		if (is_string($rules_version) && $rules_version !== '') {
			$where .= ' AND r.rules_version = %s';
			$params[] = $rules_version;
		}

		$prepared = $params !== [] ? $wpdb->prepare($where, $params) : $where;
		$query = "SELECT r.{$field} AS bucket, COUNT(*) AS item_count, SUM(CASE WHEN i.review_label = 'correct' THEN 1 ELSE 0 END) AS correct_count FROM {$items} i INNER JOIN {$reviews} r ON r.id = i.review_id {$prepared} GROUP BY r.{$field} ORDER BY item_count DESC, bucket ASC";
		$rows = $wpdb->get_results($query, ARRAY_A);
		return is_array($rows) ? $rows : [];
	}

	private function bool_to_nullable_int(mixed $value): ?int {
		if ($value === null || $value === '') {
			return null;
		}
		return $value ? 1 : 0;
	}

	private function decode_json(string $json): array {
		$decoded = json_decode($json, true);
		return is_array($decoded) ? $decoded : [];
	}
}
