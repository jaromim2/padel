<?php

if (!defined('ABSPATH')) {
	exit;
}

final class Padel_Video_Analysis_Storage {
	private static ?self $instance = null;

	public static function instance(): self {
		if (self::$instance === null) {
			self::$instance = new self();
		}

		return self::$instance;
	}

	public function get_private_root(): string {
		$uploads = wp_upload_dir();
		return trailingslashit($uploads['basedir']) . 'padel-video-analysis';
	}

	public function ensure_private_directory(): string {
		$root = $this->get_private_root();
		if (!wp_mkdir_p($root)) {
			return $root;
		}

		$index_file = trailingslashit($root) . 'index.php';
		if (!file_exists($index_file)) {
			file_put_contents($index_file, "<?php\n// Silence is golden.\n");
		}

		$htaccess = trailingslashit($root) . '.htaccess';
		if (!file_exists($htaccess)) {
			$rules = "Options -Indexes\n<FilesMatch \"\\.(mp4|mov|webm)$\">\nRequire all denied\n</FilesMatch>\n";
			file_put_contents($htaccess, $rules);
		}

		return $root;
	}

	public function get_artifact_cache_root(): string {
		return trailingslashit($this->ensure_private_directory()) . 'artifact-cache';
	}

	public function get_artifact_cache_path(int $analysis_id, string $artifact_name, string $extension): string {
		$root = trailingslashit($this->get_artifact_cache_root()) . absint($analysis_id);
		if (!wp_mkdir_p($root)) {
			return trailingslashit($root) . sanitize_file_name($artifact_name) . '.' . ltrim($extension, '.');
		}

		return trailingslashit($root) . sanitize_file_name($artifact_name) . '.' . ltrim($extension, '.');
	}

	public function validate_and_store_upload(
		array $file,
		int $analysis_id,
		int $user_id,
		?int $max_file_size = null,
		?float $max_duration_seconds = null
	): array|WP_Error {
		if (!isset($file['error']) || (int) $file['error'] !== UPLOAD_ERR_OK) {
			return new WP_Error('padel_upload_failed', 'The upload failed.', ['status' => 400]);
		}

		$tmp_name = (string) ($file['tmp_name'] ?? '');
		$original_name = (string) ($file['name'] ?? '');
		$size = (int) ($file['size'] ?? 0);

		if ($tmp_name === '' || !is_uploaded_file($tmp_name)) {
			return new WP_Error('padel_invalid_upload', 'Invalid uploaded file.', ['status' => 400]);
		}

		$max_file_size = $max_file_size ?? Padel_Video_Analysis::MAX_FILE_SIZE;
		$max_duration_seconds = $max_duration_seconds ?? Padel_Video_Analysis::MAX_DURATION_SECONDS;

		if ($size <= 0 || $size > $max_file_size) {
			return new WP_Error('padel_file_too_large', sprintf('Video must be %s or smaller.', size_format($max_file_size)), ['status' => 413]);
		}

		$allowed_exts = ['mp4', 'mov', 'webm'];
		$detected = wp_check_filetype_and_ext($tmp_name, $original_name, [
			'mp4' => 'video/mp4',
			'mov' => 'video/quicktime',
			'webm' => 'video/webm',
		]);

		$ext = strtolower((string) ($detected['ext'] ?? pathinfo($original_name, PATHINFO_EXTENSION)));
		$type = strtolower((string) ($detected['type'] ?? ''));

		if (!in_array($ext, $allowed_exts, true)) {
			return new WP_Error('padel_invalid_extension', 'Only MP4, MOV, and WebM files are allowed.', ['status' => 400]);
		}

		if ($type === '' || strpos($type, 'video/') !== 0) {
			return new WP_Error('padel_invalid_mime', 'The uploaded file is not a valid video.', ['status' => 400]);
		}

		$finfo = finfo_open(FILEINFO_MIME_TYPE);
		$probe_mime = $finfo ? (string) finfo_file($finfo, $tmp_name) : '';
		if (is_resource($finfo)) {
			finfo_close($finfo);
		}

		if ($probe_mime === '' || strpos($probe_mime, 'video/') !== 0) {
			return new WP_Error('padel_invalid_video', 'The uploaded file is not a readable video.', ['status' => 400]);
		}

		$duration = $this->read_duration_seconds($tmp_name);
		if (is_wp_error($duration)) {
			return $duration;
		}

		if ($duration <= 0 || $duration > $max_duration_seconds) {
			return new WP_Error('padel_duration_too_long', sprintf('Video must be %.0f seconds or shorter.', $max_duration_seconds), ['status' => 400]);
		}

		$root = $this->ensure_private_directory();
		$original_base = sanitize_file_name(pathinfo($original_name, PATHINFO_FILENAME));
		if ($original_base === '') {
			$original_base = 'padel-video';
		}

		$sha256 = hash_file('sha256', $tmp_name);
		$target_name = sprintf(
			'analysis-%d-user-%d-%s-%s.%s',
			$analysis_id,
			$user_id,
			$original_base,
			substr($sha256 ?: wp_generate_password(12, false, false), 0, 12),
			$ext
		);

		$target_path = trailingslashit($root) . $target_name;

		if (!@move_uploaded_file($tmp_name, $target_path)) {
			return new WP_Error('padel_move_failed', 'Unable to move uploaded video into private storage.', ['status' => 500]);
		}

		@chmod($target_path, 0600);

		return [
			'path' => $target_path,
			'name' => $target_name,
			'mime_type' => $probe_mime,
			'size' => $size,
			'duration_seconds' => $duration,
			'sha256' => $sha256 ?: '',
		];
	}

	public function read_duration_seconds(string $path): float|WP_Error {
		$ffprobe = trim((string) shell_exec('command -v ffprobe 2>/dev/null'));
		if ($ffprobe === '') {
			return new WP_Error('padel_ffprobe_missing', 'Video duration probe is not available on this server.', ['status' => 500]);
		}

		$command = sprintf(
			'%s -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 %s 2>/dev/null',
			escapeshellcmd($ffprobe),
			escapeshellarg($path)
		);

		$output = trim((string) shell_exec($command));
		if ($output === '' || !is_numeric($output)) {
			return new WP_Error('padel_duration_probe_failed', 'Unable to read video duration.', ['status' => 400]);
		}

		return (float) $output;
	}

	public function delete_private_file(string $path): bool {
		if ($path === '' || !file_exists($path)) {
			return true;
		}

		$root = realpath($this->get_private_root());
		$real = realpath($path);
		if ($root === false || $real === false || strpos($real, $root) !== 0) {
			return false;
		}

		return @unlink($real);
	}

	public function delete_artifact_cache(int $analysis_id): void {
		$root = trailingslashit($this->get_artifact_cache_root()) . absint($analysis_id);
		if (!is_dir($root)) {
			return;
		}
		$this->delete_tree($root);
	}

	private function delete_tree(string $path): void {
		$items = glob(trailingslashit($path) . '*');
		if (is_array($items)) {
			foreach ($items as $item) {
				if (is_dir($item)) {
					$this->delete_tree($item);
				} else {
					@unlink($item);
				}
			}
		}
		@rmdir($path);
	}
}
