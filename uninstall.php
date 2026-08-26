<?php

if (!defined('WP_UNINSTALL_PLUGIN')) {
	exit;
}

require_once __DIR__ . '/includes/class-padel-video-analysis-storage.php';
require_once __DIR__ . '/includes/class-padel-video-analysis-review-store.php';
require_once __DIR__ . '/includes/class-padel-video-analysis-cron.php';
require_once __DIR__ . '/includes/class-padel-video-analysis.php';
require_once __DIR__ . '/includes/class-padel-match-analysis.php';

$posts = get_posts([
	'post_type' => Padel_Video_Analysis::CPT,
	'post_status' => ['publish', 'private', 'draft'],
	'numberposts' => -1,
	'fields' => 'ids',
]);

if (is_array($posts)) {
	$storage = Padel_Video_Analysis_Storage::instance();
	foreach ($posts as $post_id) {
		$post_id = (int) $post_id;
		$storage->delete_private_file((string) get_post_meta($post_id, 'padel_video_analysis_video_path', true));
		wp_delete_post($post_id, true);
	}
}

$page_id = (int) get_option(Padel_Video_Analysis::PAGE_OPTION, 0);
if ($page_id > 0) {
	wp_delete_post($page_id, true);
}

$match_page_id = (int) get_option(Padel_Match_Analysis::PAGE_OPTION, 0);
if ($match_page_id > 0) {
	wp_delete_post($match_page_id, true);
}

delete_option(Padel_Video_Analysis::PAGE_OPTION);
delete_option(Padel_Match_Analysis::PAGE_OPTION);

global $wpdb;
$wpdb->query('DROP TABLE IF EXISTS ' . Padel_Video_Analysis_Review_Store::instance()->review_items_table_name());
$wpdb->query('DROP TABLE IF EXISTS ' . Padel_Video_Analysis_Review_Store::instance()->reviews_table_name());
delete_option('padel_video_analysis_review_schema_version');

$role = get_role('administrator');
if ($role instanceof WP_Role) {
	$role->remove_cap(Padel_Video_Analysis::REVIEW_CAPABILITY);
}
