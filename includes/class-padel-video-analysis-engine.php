<?php

if (!defined('ABSPATH')) {
	exit;
}

final class Padel_Video_Analysis_Engine {
	public const ENGINE_NAME = 'local_php_engine';
	public const ENGINE_VERSION = '0.2.0';
	private static ?self $instance = null;

	public static function instance(): self {
		if (self::$instance === null) {
			self::$instance = new self();
		}

		return self::$instance;
	}

	private function __construct() {
	}

	public function process(int $analysis_id): array|WP_Error {
		$analysis = get_post($analysis_id);
		if (!$analysis instanceof WP_Post || $analysis->post_type !== Padel_Video_Analysis::CPT) {
			return new WP_Error('padel_invalid_analysis', 'Analysis record not found.', ['status' => 404]);
		}

		$storage = Padel_Video_Analysis_Storage::instance();
		$input_path = (string) Padel_Video_Analysis::instance()->get_meta($analysis_id, 'video_path', '');
		if ($input_path === '' || !file_exists($input_path)) {
			return new WP_Error('padel_missing_video', 'Uploaded video could not be found.', ['status' => 404]);
		}

		$probe = $this->probe_video($input_path);
		if (is_wp_error($probe)) {
			return $probe;
		}

		$normalized_path = $this->normalize_video($input_path, $analysis_id);
		if (is_wp_error($normalized_path)) {
			return $normalized_path;
		}

		$thumbnail_path = $this->extract_thumbnail($input_path, $analysis_id, (float) ($probe['duration_seconds'] ?? 0));
		if (is_wp_error($thumbnail_path)) {
			$storage->delete_private_file($normalized_path);
			return $thumbnail_path;
		}

		$result = $this->build_analysis_result($analysis_id, $probe);

		return [
			'engine_name' => self::ENGINE_NAME,
			'engine_version' => self::ENGINE_VERSION,
			'normalized_video_path' => $normalized_path,
			'poster_frame_path' => $thumbnail_path,
			'result' => $result,
			'probe' => $probe,
		];
	}

	private function probe_video(string $path): array|WP_Error {
		$ffprobe = trim((string) shell_exec('command -v ffprobe 2>/dev/null'));
		if ($ffprobe === '') {
			return new WP_Error('padel_ffprobe_missing', 'Video probe tool is not available on this server.', ['status' => 500]);
		}

		$command = sprintf(
			'%s -v error -print_format json -show_format -show_streams %s 2>/dev/null',
			escapeshellcmd($ffprobe),
			escapeshellarg($path)
		);

		$output = trim((string) shell_exec($command));
		$decoded = json_decode($output, true);
		if (!is_array($decoded)) {
			return new WP_Error('padel_probe_failed', 'Unable to read video metadata.', ['status' => 400]);
		}

		$video_stream = null;
		foreach (($decoded['streams'] ?? []) as $stream) {
			if (is_array($stream) && (($stream['codec_type'] ?? '') === 'video')) {
				$video_stream = $stream;
				break;
			}
		}

		if (!is_array($video_stream)) {
			return new WP_Error('padel_no_video_stream', 'No usable video stream was detected.', ['status' => 400]);
		}

		$duration = (float) ($decoded['format']['duration'] ?? 0);
		$width = (int) ($video_stream['width'] ?? 0);
		$height = (int) ($video_stream['height'] ?? 0);
		$rotation = (int) ($video_stream['tags']['rotate'] ?? 0);
		$fps = $this->parse_fraction((string) ($video_stream['avg_frame_rate'] ?? $video_stream['r_frame_rate'] ?? '0/1'));

		return [
			'duration_seconds' => $duration,
			'width' => $width,
			'height' => $height,
			'rotation' => $rotation,
			'fps' => $fps,
			'codec' => (string) ($video_stream['codec_name'] ?? ''),
			'has_audio' => $this->has_audio_stream($decoded['streams'] ?? []),
		];
	}

	private function normalize_video(string $input_path, int $analysis_id): string|WP_Error {
		$ffmpeg = trim((string) shell_exec('command -v ffmpeg 2>/dev/null'));
		if ($ffmpeg === '') {
			return new WP_Error('padel_ffmpeg_missing', 'Video normalization tool is not available on this server.', ['status' => 500]);
		}

		$root = Padel_Video_Analysis_Storage::instance()->ensure_private_directory();
		$normalized_path = trailingslashit($root) . sprintf('analysis-%d-normalized.mp4', $analysis_id);

		$command = sprintf(
			'%s -y -i %s -an -vf %s -c:v libx264 -preset veryfast -crf 24 -movflags +faststart %s 2>/dev/null',
			escapeshellcmd($ffmpeg),
			escapeshellarg($input_path),
			escapeshellarg("scale='min(1280,iw)':-2,fps=30,setsar=1"),
			escapeshellarg($normalized_path)
		);

		$output = [];
		$return_code = 0;
		@exec($command, $output, $return_code);
		if ($return_code !== 0 || !file_exists($normalized_path)) {
			return new WP_Error('padel_normalization_failed', 'Unable to normalize the uploaded video.', ['status' => 500]);
		}

		@chmod($normalized_path, 0600);
		return $normalized_path;
	}

	private function extract_thumbnail(string $input_path, int $analysis_id, float $duration): string|WP_Error {
		$ffmpeg = trim((string) shell_exec('command -v ffmpeg 2>/dev/null'));
		if ($ffmpeg === '') {
			return new WP_Error('padel_ffmpeg_missing', 'Thumbnail extraction tool is not available on this server.', ['status' => 500]);
		}

		$root = Padel_Video_Analysis_Storage::instance()->ensure_private_directory();
		$thumbnail_path = trailingslashit($root) . sprintf('analysis-%d-poster.jpg', $analysis_id);
		$seek = $duration > 2 ? max(1.0, $duration / 2.0) : 0.5;

		$command = sprintf(
			'%s -y -ss %s -i %s -frames:v 1 -q:v 2 %s 2>/dev/null',
			escapeshellcmd($ffmpeg),
			escapeshellarg((string) $seek),
			escapeshellarg($input_path),
			escapeshellarg($thumbnail_path)
		);

		$output = [];
		$return_code = 0;
		@exec($command, $output, $return_code);
		if ($return_code !== 0 || !file_exists($thumbnail_path)) {
			return new WP_Error('padel_thumbnail_failed', 'Unable to extract a preview frame.', ['status' => 500]);
		}

		@chmod($thumbnail_path, 0600);
		return $thumbnail_path;
	}

	private function build_analysis_result(int $analysis_id, array $probe): array {
		$duration = (float) ($probe['duration_seconds'] ?? 0);
		$width = (int) ($probe['width'] ?? 0);
		$height = (int) ($probe['height'] ?? 0);
		$fps = (float) ($probe['fps'] ?? 0);
		$rotation = (int) ($probe['rotation'] ?? 0);
		$orientation = $width >= $height ? 'landscape' : 'portrait';

		$findings = [
			sprintf('Video metadata confirmed: %s %dx%d at %.1f FPS.', $orientation, $width, $height, $fps),
		];

		if ($rotation !== 0) {
			$findings[] = sprintf('Rotation metadata detected (%d degrees) and applied during normalization.', $rotation);
		}

		if ($duration >= 20) {
			$findings[] = 'Longer capture gives more room for future pose analysis.';
		}

		$recommendations = [
			'Use the same camera angle for future comparisons.',
			'Keep the camera stable and centered on the hitting zone.',
		];

		if ($fps < 30) {
			$recommendations[] = 'Try recording at 30 FPS or higher for cleaner frame analysis.';
		}

		return [
			'summary' => 'Local engine processed the video successfully. This output is deterministic and ready for a future Python analyzer.',
			'findings' => $findings,
			'recommendations' => $recommendations,
			'analysis_id' => $analysis_id,
			'engine' => self::ENGINE_NAME,
			'engine_version' => self::ENGINE_VERSION,
			'video' => $probe,
		];
	}

	private function parse_fraction(string $value): float {
		if (str_contains($value, '/')) {
			[$num, $den] = array_pad(explode('/', $value, 2), 2, '1');
			$den = (float) $den;
			if ($den == 0.0) {
				return 0.0;
			}
			return (float) $num / $den;
		}

		return is_numeric($value) ? (float) $value : 0.0;
	}

	private function has_audio_stream(array $streams): bool {
		foreach ($streams as $stream) {
			if (is_array($stream) && (($stream['codec_type'] ?? '') === 'audio')) {
				return true;
			}
		}

		return false;
	}
}
