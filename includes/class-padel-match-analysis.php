<?php

if (!defined('ABSPATH')) {
	exit;
}

final class Padel_Match_Analysis {
	public const PAGE_SLUG = 'padel-match-analysis';
	public const PAGE_TITLE = 'ניתוח משחק';
	public const PAGE_OPTION = 'padel_video_analysis_match_page_id';
	public const SHORTCODE_TAG = 'padel_match_analysis';

	private static ?self $instance = null;

	public static function instance(): self {
		if (self::$instance === null) {
			self::$instance = new self();
		}

		return self::$instance;
	}

	public static function activate(): void {
		Padel_Video_Analysis::instance()->register_post_type();
		$instance = self::instance();
		$instance->maybe_create_page(true);
		flush_rewrite_rules();
	}

	public static function deactivate(): void {
		flush_rewrite_rules();
	}

	private function __construct() {
		add_action('init', [$this, 'maybe_create_page']);
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
					'post_content' => '[' . self::SHORTCODE_TAG . ']',
				]);
			} else {
				$page_id = (int) wp_insert_post([
					'post_type' => 'page',
					'post_status' => 'publish',
					'post_title' => self::PAGE_TITLE,
					'post_name' => self::PAGE_SLUG,
					'post_content' => '[' . self::SHORTCODE_TAG . ']',
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

	public function get_user_analysis_posts(int $user_id, int $limit = 10): array {
		return Padel_Video_Analysis::instance()->get_user_analysis_posts($user_id, $limit, 'match');
	}

	public function create_analysis(int $user_id, array $payload): WP_Post|WP_Error {
		return Padel_Video_Analysis::instance()->create_analysis($user_id, $payload, 'match');
	}

	public function get_upload_limits(): array {
		return [
			'max_file_size' => Padel_Video_Analysis::MAX_MATCH_FILE_SIZE,
			'max_duration_seconds' => Padel_Video_Analysis::MAX_MATCH_DURATION_SECONDS,
		];
	}

	public function get_record_payload(WP_Post $post): array {
		return Padel_Video_Analysis::instance()->get_record_payload($post);
	}
}
