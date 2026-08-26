<?php

if (!defined('ABSPATH')) {
	exit;
}

final class Padel_Match_Analysis_Shortcode {
	public const SHORTCODE_TAG = 'padel_match_analysis';

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

		$plugin = Padel_Match_Analysis::instance();
		$records = $plugin->get_user_analysis_posts(get_current_user_id(), 6);

		wp_enqueue_style(
			'padel-video-analysis-frontend',
			PADEL_VIDEO_ANALYSIS_URL . 'assets/css/frontend.css',
			[],
			PADEL_VIDEO_ANALYSIS_VERSION
		);

		wp_enqueue_style(
			'padel-video-analysis-match',
			PADEL_VIDEO_ANALYSIS_URL . 'assets/css/match-frontend.css',
			['padel-video-analysis-frontend'],
			PADEL_VIDEO_ANALYSIS_VERSION
		);

		wp_enqueue_script(
			'padel-video-analysis-match',
			PADEL_VIDEO_ANALYSIS_URL . 'assets/js/match-frontend.js',
			[],
			PADEL_VIDEO_ANALYSIS_VERSION,
			true
		);

		$data = [
			'restUrl' => esc_url_raw(rest_url('padel-video-analysis/v1')),
			'nonce' => wp_create_nonce('wp_rest'),
			'pageUrl' => esc_url_raw($plugin->get_page_url()),
			'artifactUrlBase' => esc_url_raw(rest_url('padel-video-analysis/v1/analysis/')),
			'selectionUrlBase' => esc_url_raw(rest_url('padel-video-analysis/v1/analysis/')),
			'reviewUrlBase' => esc_url_raw(rest_url('padel-video-analysis/v1/analysis/')),
			'statuses' => Padel_Video_Analysis::instance()->get_status_labels(),
			'allowedHands' => Padel_Video_Analysis::instance()->get_allowed_hands(),
			'allowedCameraAngles' => Padel_Video_Analysis::instance()->get_allowed_camera_angles(),
			'uploadLimits' => $plugin->get_upload_limits(),
			'strings' => [
				'uploading' => 'Uploading match video...',
				'processing' => 'Processing preview...',
				'awaitingSelection' => 'Select a player to continue.',
				'completed' => 'Completed',
				'failed' => 'Failed',
				'deleteConfirm' => 'Delete this match analysis and its private video?',
				'uploadSuccess' => 'Match video uploaded successfully.',
				'uploadError' => 'Upload failed.',
				'selectionSuccess' => 'Player selected successfully.',
				'reviewSuccess' => 'Review saved.',
				'artifactLoadError' => 'Unable to load the private artifact.',
			],
		];

		wp_add_inline_script(
			'padel-video-analysis-match',
			'window.PadelMatchAnalysis = ' . wp_json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . ';',
			'before'
		);

		ob_start();
		?>
		<div class="padel-video-analysis-shell padel-match-shell" dir="rtl">
			<div class="padel-video-analysis-hero">
				<div>
					<p class="padel-kicker">Padel MVP</p>
					<h1>ניתוח משחק</h1>
					<p class="padel-lead">העלה וידאו מלא, בחר שחקן אחד, וקבל חלונות חבטה פרטיים עם סימון ובדיקה ידנית.</p>
				</div>
				<div class="padel-hero-chip">
					<span>Phase 2</span>
					<strong>Match tracking preview</strong>
				</div>
			</div>

			<div class="padel-grid">
				<section class="padel-card">
					<h2>העלאת משחק</h2>
					<form id="padel-match-analysis-form" class="padel-form">
						<input type="hidden" name="shot_type" value="forehand">
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
								<option value="side">Side</option>
								<option value="baseline">Baseline</option>
								<option value="rear">Rear</option>
							</select>
						</label>
						<label>
							<span>קובץ וידאו</span>
							<input name="video_file" type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" required>
							<small>וידאו מלא, פרופיל קבוע, 4 שחקנים נראים ברוב הזמן. התמיכה המקסימלית מוצגת במסך זה.</small>
						</label>
						<button type="submit" class="padel-button">העלה משחק</button>
						<div class="padel-progress-wrap" aria-live="polite">
							<div class="padel-progress-meta">
								<span id="padel-match-upload-state">מוכן להעלאה</span>
								<span id="padel-match-upload-percent">0%</span>
							</div>
							<div class="padel-progress-bar">
								<div id="padel-match-upload-fill" class="padel-progress-fill" style="width:0%"></div>
							</div>
						</div>
						<p id="padel-match-upload-message" class="padel-message"></p>
					</form>
				</section>

				<section class="padel-card">
					<h2>סטטוס אחרון</h2>
					<div id="padel-match-active-analysis" class="padel-active-analysis" data-empty="<?php echo esc_attr(empty($records) ? '1' : '0'); ?>">
						<?php
						if (empty($records)) {
							echo '<p class="padel-empty">עדיין לא הועלה משחק.</p>';
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
		$login_url = wp_login_url(Padel_Match_Analysis::instance()->get_page_url());
		return sprintf(
			'<div class="padel-video-analysis-shell" dir="rtl"><div class="padel-card"><h2>נדרשת התחברות</h2><p>כדי להעלות משחק יש להתחבר לחשבון WordPress.</p><p><a class="padel-button" href="%s">לעמוד ההתחברות</a></p></div></div>',
			esc_url($login_url)
		);
	}

	public function render_record_card(array $record): string {
		$status_class = 'is-' . sanitize_html_class((string) ($record['status'] ?? 'uploaded'));
		$progress = (int) ($record['progress'] ?? 0);
		$record_json = esc_attr(wp_json_encode($record, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));

		$html  = '<article class="padel-analysis-card padel-match-card" data-analysis-id="' . esc_attr((string) ($record['id'] ?? 0)) . '" data-status="' . esc_attr((string) ($record['status'] ?? 'uploaded')) . '" data-record="' . $record_json . '">';
		$html .= '<div class="padel-analysis-head">';
		$html .= '<div><p class="padel-card-kicker">Match #' . esc_html((string) ($record['id'] ?? 0)) . '</p><h3>' . esc_html((string) ($record['title'] ?? '')) . '</h3></div>';
		$html .= '<span class="padel-status-badge ' . esc_attr($status_class) . '">' . esc_html((string) ($record['status_label'] ?? '')) . '</span>';
		$html .= '</div>';
		$html .= '<div class="padel-analysis-meta">';
		$html .= '<span>Hand: ' . esc_html((string) ($record['dominant_hand_label'] ?? '')) . '</span>';
		$html .= '<span>Angle: ' . esc_html((string) ($record['camera_angle_label'] ?? '')) . '</span>';
		$html .= '<span>Mode: ' . esc_html((string) ($record['analysis_mode_label'] ?? '')) . '</span>';
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
		$html .= '<div class="padel-match-result"></div>';
		$html .= '<div class="padel-actions"><button type="button" class="padel-button secondary padel-delete-analysis" data-analysis-id="' . esc_attr((string) ($record['id'] ?? 0)) . '">מחק משחק ורשומה</button></div>';
		$html .= '</article>';

		return $html;
	}
}
