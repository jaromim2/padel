<?php
/**
 * Plugin Name: Padel Video Analysis
 * Description: Logged-in Padel shot analysis MVP with private video uploads and mock processing.
 * Version: 0.1.7
 * Author: OpenAI
 * Text Domain: padel-video-analysis
 */

if (!defined('ABSPATH')) {
	exit;
}

define('PADEL_VIDEO_ANALYSIS_VERSION', '0.1.7');
define('PADEL_VIDEO_ANALYSIS_FILE', __FILE__);
define('PADEL_VIDEO_ANALYSIS_PATH', plugin_dir_path(__FILE__));
define('PADEL_VIDEO_ANALYSIS_URL', plugin_dir_url(__FILE__));

require_once PADEL_VIDEO_ANALYSIS_PATH . 'includes/class-padel-video-analysis-storage.php';
require_once PADEL_VIDEO_ANALYSIS_PATH . 'includes/class-padel-video-analysis-review-store.php';
require_once PADEL_VIDEO_ANALYSIS_PATH . 'includes/class-padel-video-analysis-external-client.php';
require_once PADEL_VIDEO_ANALYSIS_PATH . 'includes/class-padel-video-analysis-engine.php';
require_once PADEL_VIDEO_ANALYSIS_PATH . 'includes/class-padel-video-analysis-cron.php';
require_once PADEL_VIDEO_ANALYSIS_PATH . 'includes/class-padel-video-analysis.php';
require_once PADEL_VIDEO_ANALYSIS_PATH . 'includes/class-padel-video-analysis-cli.php';
require_once PADEL_VIDEO_ANALYSIS_PATH . 'includes/class-padel-video-analysis-rest.php';
require_once PADEL_VIDEO_ANALYSIS_PATH . 'includes/class-padel-video-analysis-shortcode.php';
require_once PADEL_VIDEO_ANALYSIS_PATH . 'includes/class-padel-match-analysis.php';
require_once PADEL_VIDEO_ANALYSIS_PATH . 'includes/class-padel-match-analysis-shortcode.php';

register_activation_hook(__FILE__, ['Padel_Video_Analysis', 'activate']);
register_deactivation_hook(__FILE__, ['Padel_Video_Analysis', 'deactivate']);

add_action('plugins_loaded', static function () {
	Padel_Video_Analysis::instance();
	if (defined('WP_CLI') && WP_CLI) {
		Padel_Video_Analysis_CLI::instance();
		WP_CLI::add_command('padel-video-analysis backfill-feedback', [Padel_Video_Analysis_CLI::instance(), 'backfill_feedback']);
	}
	Padel_Video_Analysis_REST::instance();
	Padel_Video_Analysis_Shortcode::instance();
	Padel_Match_Analysis::instance();
	Padel_Match_Analysis_Shortcode::instance();
	Padel_Video_Analysis_Cron::instance();
});
