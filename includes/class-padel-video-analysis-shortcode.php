<?php

if (!defined('ABSPATH')) {
	exit;
}

final class Padel_Video_Analysis_Shortcode {
	public const SHORTCODE_TAG = 'padel_video_analysis';

	private static ?self $instance = null;

	public static function instance(): self {
		if (self::$instance === null) {
			self::$instance = new self();
		}

		return self::$instance;
	}

	private function __construct() {
		add_shortcode(self::SHORTCODE_TAG, [$this, 'render']);
	}

	public function render(): string {
		if (!is_user_logged_in()) {
			return $this->render_login_prompt();
		}

		$plugin = Padel_Video_Analysis::instance();
		$records = $plugin->get_user_analysis_posts(get_current_user_id(), 6);
		$reviewer_mode = $plugin->can_current_user_review();
		$review_queue = $reviewer_mode ? $plugin->get_recent_analysis_posts(10, 'stroke', ['completed']) : [];
		$review_rule_catalog = $reviewer_mode ? $plugin->get_review_rule_catalog() : ['version' => '', 'rules' => []];
		$calibration_summary = $reviewer_mode ? $plugin->get_review_calibration_summary() : ['rules_version' => '', 'rule_rows' => [], 'grouped' => []];

		wp_enqueue_style(
			'padel-video-analysis-frontend',
			PADEL_VIDEO_ANALYSIS_URL . 'assets/css/frontend.css',
			[],
			PADEL_VIDEO_ANALYSIS_VERSION
		);

		wp_enqueue_script(
			'padel-video-analysis-frontend',
			PADEL_VIDEO_ANALYSIS_URL . 'assets/js/frontend.js',
			[],
			PADEL_VIDEO_ANALYSIS_VERSION,
			true
		);

		$data = [
			'restUrl' => esc_url_raw(rest_url('padel-video-analysis/v1')),
			'nonce' => wp_create_nonce('wp_rest'),
			'pageUrl' => esc_url_raw($plugin->get_page_url()),
			'artifactUrlBase' => esc_url_raw(rest_url('padel-video-analysis/v1/analysis/')),
			'contactFrameUrlBase' => esc_url_raw(rest_url('padel-video-analysis/v1/analysis/')),
			'reviewUrlBase' => esc_url_raw(rest_url('padel-video-analysis/v1/analysis/')),
			'reviewerMode' => $reviewer_mode,
			'reviewRuleCatalog' => $review_rule_catalog,
			'calibrationSummary' => $calibration_summary,
			'statuses' => $plugin->get_status_labels(),
			'allowedShotTypes' => $plugin->get_allowed_shot_types(),
			'allowedHands' => $plugin->get_allowed_hands(),
			'allowedCameraAngles' => $plugin->get_allowed_camera_angles(),
			'strings' => [
				'uploading' => 'Uploading video...',
				'processing' => 'Processing...',
				'completed' => 'Completed',
				'failed' => 'Failed',
				'deleteConfirm' => 'Delete this analysis and its private video?',
				'uploadSuccess' => 'Video uploaded successfully.',
				'uploadError' => 'Upload failed.',
				'contactFrameUpdated' => 'Contact frame updated successfully.',
				'reviewSaved' => 'Review saved successfully.',
				'reviewSaveFailed' => 'Unable to save review.',
				'artifactLoadError' => 'Unable to load the private artifact.',
			],
		];

		wp_add_inline_script(
			'padel-video-analysis-frontend',
			'window.PadelVideoAnalysis = ' . wp_json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . ';',
			'before'
		);

		ob_start();
		?>
		<div class="padel-video-analysis-shell" dir="rtl">
			<div class="padel-video-analysis-hero">
				<div>
					<p class="padel-kicker">Padel MVP</p>
					<h1>ניתוח חבטה</h1>
					<p class="padel-lead">העלה וידאו קצר, עקוב אחר הסטטוס, ומחק את הרשומה והקובץ בכל שלב.</p>
				</div>
				<div class="padel-hero-chip">
					<span>Phase 1</span>
					<strong>Mock processing</strong>
				</div>
			</div>

			<div class="padel-grid">
				<section class="padel-card">
					<h2>העלאת סרטון</h2>
					<form id="padel-video-analysis-form" class="padel-form">
						<label>
							<span>סוג חבטה</span>
							<select name="shot_type" required>
								<option value="forehand">Forehand</option>
							</select>
						</label>
						<label>
							<span>יד דומיננטית</span>
							<select name="dominant_hand" required>
								<option value="right">Right-handed</option>
								<option value="left">Left-handed</option>
							</select>
						</label>
						<label>
							<span>זווית צילום</span>
							<select name="camera_angle" required>
								<option value="baseline">Baseline</option>
								<option value="side">Side</option>
								<option value="rear">Rear</option>
							</select>
						</label>
						<label>
							<span>קובץ וידאו</span>
							<input name="video_file" type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" required>
							<small>MP4, MOV או WebM עד 35 שניות ו-75MB.</small>
						</label>
						<button type="submit" class="padel-button">העלה סרטון</button>
						<div class="padel-progress-wrap" aria-live="polite">
							<div class="padel-progress-meta">
								<span id="padel-upload-state">מוכן להעלאה</span>
								<span id="padel-upload-percent">0%</span>
							</div>
							<div class="padel-progress-bar">
								<div id="padel-upload-fill" class="padel-progress-fill" style="width:0%"></div>
							</div>
						</div>
						<p id="padel-upload-message" class="padel-message"></p>
					</form>
				</section>

				<section class="padel-card">
					<h2>סטטוס אחרון</h2>
					<div id="padel-active-analysis" class="padel-active-analysis" data-empty="<?php echo esc_attr(empty($records) ? '1' : '0'); ?>">
						<?php
						if ($reviewer_mode) {
							echo '<div class="padel-review-hub"><h3>Calibration review queue</h3><p>Visible only to reviewers. Reviews are stored separately from automatic analysis results.</p></div>';
							foreach ($review_queue as $record) {
								echo $this->render_record_card($plugin->get_record_payload($record));
							}
						}
						if (empty($records)) {
							echo '<p class="padel-empty">עדיין לא הועלה סרטון.</p>';
						}
						foreach ($records as $record) {
							echo $this->render_record_card($plugin->get_record_payload($record));
						}
						?>
					</div>
				</section>
			</div>
		</div>
		<?php
		return (string) ob_get_clean();
	}

	private function render_login_prompt(): string {
		$login_url = wp_login_url(Padel_Video_Analysis::instance()->get_page_url());
		return sprintf(
			'<div class="padel-video-analysis-shell" dir="rtl"><div class="padel-card"><h2>נדרשת התחברות</h2><p>כדי להעלות סרטון יש להתחבר לחשבון WordPress.</p><p><a class="padel-button" href="%s">לעמוד ההתחברות</a></p></div></div>',
			esc_url($login_url)
		);
	}

	public function render_record_card(array $record): string {
		$status_class = 'is-' . sanitize_html_class((string) ($record['status'] ?? 'uploaded'));
		$progress = (int) ($record['progress'] ?? 0);
		$result = is_array($record['result'] ?? null) ? $record['result'] : [];
		$record_json = esc_attr(wp_json_encode($record, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
		$result_html = '';

		if (!empty($result)) {
			$summary = esc_html((string) ($result['summary'] ?? ''));
			$findings = '';
			if (!empty($result['findings']) && is_array($result['findings'])) {
				$items = array_map(static fn($item) => '<li>' . esc_html((string) $item) . '</li>', $result['findings']);
				$findings = '<ul>' . implode('', $items) . '</ul>';
			}
			$recommendations = '';
			if (!empty($result['recommendations']) && is_array($result['recommendations'])) {
				$items = array_map(static fn($item) => '<li>' . esc_html((string) $item) . '</li>', $result['recommendations']);
				$recommendations = '<ul>' . implode('', $items) . '</ul>';
			}

			$result_html = '<div class="padel-result"><strong>תוצאת דמה</strong><p>' . $summary . '</p>' . $findings . $recommendations . '</div>';
		}

		$html  = '<article class="padel-analysis-card" data-analysis-id="' . esc_attr((string) ($record['id'] ?? 0)) . '" data-status="' . esc_attr((string) ($record['status'] ?? 'uploaded')) . '" data-record="' . $record_json . '">';
		$html .= '<div class="padel-analysis-head">';
		$html .= '<div><p class="padel-card-kicker">Analysis #' . esc_html((string) ($record['id'] ?? 0)) . '</p><h3>' . esc_html((string) ($record['title'] ?? '')) . '</h3></div>';
		$html .= '<span class="padel-status-badge ' . esc_attr($status_class) . '">' . esc_html((string) ($record['status_label'] ?? '')) . '</span>';
		$html .= '</div>';
		$html .= '<div class="padel-analysis-meta">';
		$html .= '<span>Shot: ' . esc_html((string) ($record['shot_type_label'] ?? '')) . '</span>';
		$html .= '<span>Hand: ' . esc_html((string) ($record['dominant_hand_label'] ?? '')) . '</span>';
		$html .= '<span>Angle: ' . esc_html((string) ($record['camera_angle_label'] ?? '')) . '</span>';
		$html .= '</div>';
		$html .= '<div class="padel-progress-wrap small">';
		$html .= '<div class="padel-progress-meta"><span>' . esc_html((string) ($record['status_label'] ?? '')) . '</span><span class="padel-card-progress">' . esc_html((string) $progress) . '%</span></div>';
		$html .= '<div class="padel-progress-bar"><div class="padel-progress-fill" style="width:' . esc_attr((string) $progress) . '%"></div></div>';
		$html .= '</div>';
		$html .= '<div class="padel-analysis-submeta">';
		$html .= '<span>' . esc_html((string) ($record['video']['name'] ?? '')) . '</span>';
		$html .= '<span>' . esc_html((string) ($record['video']['size_human'] ?? '0 B')) . '</span>';
		$html .= '<span>' . esc_html((string) ($record['video']['duration_seconds'] ?? 0)) . ' sec</span>';
		$html .= '</div>';
		$html .= $result_html;
		$html .= '<div class="padel-actions"><button type="button" class="padel-button secondary padel-delete-analysis" data-analysis-id="' . esc_attr((string) ($record['id'] ?? 0)) . '">מחק סרטון ורשומה</button></div>';
		$html .= '</article>';

		return $html;
	}
}
