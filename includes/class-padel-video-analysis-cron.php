<?php

if (!defined('ABSPATH')) {
	exit;
}

final class Padel_Video_Analysis_Cron {
	private static ?self $instance = null;

	public static function instance(): self {
		if (self::$instance === null) {
			self::$instance = new self();
		}

		return self::$instance;
	}

	public static function unschedule_all(): void {
		$posts = get_posts([
			'post_type' => Padel_Video_Analysis::CPT,
			'post_status' => ['publish', 'private', 'draft'],
			'numberposts' => -1,
			'fields' => 'ids',
		]);

		foreach ($posts as $post_id) {
			self::unschedule_analysis((int) $post_id);
		}
	}

	public static function unschedule_analysis(int $analysis_id): void {
		wp_clear_scheduled_hook('padel_video_analysis_advance', [$analysis_id]);
		wp_clear_scheduled_hook('padel_video_analysis_submit_external_job', [$analysis_id]);
	}

	private function __construct() {
		add_action('padel_video_analysis_advance', [$this, 'advance_analysis_from_cron'], 10, 1);
		add_action('padel_video_analysis_submit_external_job', [$this, 'submit_external_job_from_cron'], 10, 1);
	}

	public function schedule_analysis(int $analysis_id): void {
		self::unschedule_analysis($analysis_id);
		wp_schedule_single_event(time() + 1, 'padel_video_analysis_advance', [$analysis_id]);
		wp_schedule_single_event(time() + 4, 'padel_video_analysis_advance', [$analysis_id]);
	}

	public function schedule_external_retry(int $analysis_id): void {
		self::unschedule_analysis($analysis_id);
		wp_schedule_single_event(time() + 15, 'padel_video_analysis_submit_external_job', [$analysis_id]);
	}

	public function advance_analysis_from_cron(int $analysis_id): void {
		Padel_Video_Analysis::instance()->maybe_advance_analysis($analysis_id);
	}

	public function submit_external_job_from_cron(int $analysis_id): void {
		Padel_Video_Analysis::instance()->submit_external_job($analysis_id);
	}
}
