(() => {
  const config = window.PadelMatchAnalysis || {};
  const form = document.getElementById('padel-match-analysis-form');
  const activeWrap = document.getElementById('padel-match-active-analysis');
  const uploadState = document.getElementById('padel-match-upload-state');
  const uploadPercent = document.getElementById('padel-match-upload-percent');
  const uploadFill = document.getElementById('padel-match-upload-fill');
  const uploadMessage = document.getElementById('padel-match-upload-message');
  const pendingPolls = new Map();

  function headers() {
    return {
      'X-WP-Nonce': config.nonce || '',
    };
  }

  function setProgress(percent, state) {
    if (uploadPercent) uploadPercent.textContent = `${percent}%`;
    if (uploadFill) uploadFill.style.width = `${percent}%`;
    if (uploadState && state) uploadState.textContent = state;
  }

  function setMessage(message, isError = false) {
    if (!uploadMessage) return;
    uploadMessage.textContent = message;
    uploadMessage.classList.toggle('is-error', isError);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function formatNumber(value, digits = 2) {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    if (Number.isNaN(number)) return '—';
    return number.toFixed(digits);
  }

  function formatTimestamp(ms) {
    if (ms === null || ms === undefined || ms === '') return '—';
    const value = Number(ms);
    if (Number.isNaN(value)) return '—';
    return `${formatNumber(value / 1000, 2)}s`;
  }

  function buildArtifactUrl(analysisId, artifact) {
    const base = config.artifactUrlBase || '';
    return `${base.replace(/\/?$/, '/')}${encodeURIComponent(String(analysisId))}/artifact/${encodeURIComponent(String(artifact))}`;
  }

  function getPreviewFrames(match) {
    const frames = Array.isArray(match?.preview?.frames) ? match.preview.frames.filter(Boolean) : [];
    if (frames.length) return frames;
    if (match?.preview) return [match.preview];
    return [];
  }

  function getPreviewFrameArtifactName(frame) {
    const frameIndex = Number(frame?.frame_index ?? 0);
    return `preview-frame-${frameIndex}`;
  }

  function getSelectedPreviewFrame(match, card) {
    const frames = getPreviewFrames(match);
    if (!frames.length) return null;
    const storedIndex = Number(card?.dataset.previewFrameIndex || match?.preview?.frame_index || frames[0].frame_index || 0);
    return frames.find((frame) => Number(frame.frame_index) === storedIndex) || frames[0];
  }

  function renderPreviewOverlay(record, frame, isSelectionRequired, width, height) {
    const candidates = Array.isArray(frame?.candidates) ? frame.candidates : [];
    return candidates.map((candidate) => {
      const box = candidate.box || {};
      const left = Math.max(0, (Number(box.x || 0) / width) * 100);
      const top = Math.max(0, (Number(box.y || 0) / height) * 100);
      const boxWidth = Math.max(4, (Number(box.width || 0) / width) * 100);
      const boxHeight = Math.max(4, (Number(box.height || 0) / height) * 100);
      return `
        <button
          type="button"
          class="padel-match-box ${isSelectionRequired ? 'is-selectable' : ''}"
          data-candidate-id="${escapeHtml(candidate.candidate_id)}"
          data-analysis-id="${escapeHtml(record.id)}"
          data-frame-index="${escapeHtml(frame.frame_index)}"
          style="left:${left}%;top:${top}%;width:${boxWidth}%;height:${boxHeight}%"
        >
          <span>${escapeHtml(candidate.label || candidate.candidate_id)}</span>
        </button>
      `;
    }).join('');
  }

  function renderPreview(record, match, selectedFrameIndex = null) {
    const frames = getPreviewFrames(match);
    const preview = match?.preview || null;
    const artifacts = match?.artifacts || {};
    const previewArtifact = artifacts.preview_image || null;
    if (!preview || !previewArtifact || !frames.length) return '';

    const selectionRequired = match?.stage === 'selection_required';
    const selectedFrame = selectedFrameIndex !== null
      ? frames.find((frame) => Number(frame.frame_index) === Number(selectedFrameIndex)) || frames[0]
      : getSelectedPreviewFrame(match, { dataset: { previewFrameIndex: String(match?.preview?.frame_index || frames[0].frame_index || 0) } });
    const width = Number(selectedFrame?.width || preview.width || 0) || 1;
    const height = Number(selectedFrame?.height || preview.height || 0) || 1;
    const selectedFrameId = Number(selectedFrame?.frame_index || preview.frame_index || 0);
    const selectedFrameArtifactUrl = selectedFrame?.artifact
      ? buildArtifactUrl(record.id, getPreviewFrameArtifactName(selectedFrame))
      : buildArtifactUrl(record.id, 'match-preview');
    const warnings = Array.isArray(selectedFrame?.reasons) ? selectedFrame.reasons : [];
    const previewWarnings = Array.isArray(preview.reasons) ? preview.reasons : [];
    const allWarnings = [...new Set([...warnings, ...previewWarnings])];

    return `
      <section class="padel-result-section padel-preview-frame-shell" data-preview-frame-index="${escapeHtml(selectedFrameId)}">
        <div class="padel-section-head">
          <h4>Preview frame</h4>
          <span>${formatTimestamp(selectedFrame?.timestamp_ms ?? preview.timestamp_ms ?? 0)}</span>
        </div>
        <div class="padel-preview-controls">
          <button type="button" class="padel-button secondary padel-preview-step" data-direction="prev">Previous</button>
          <button type="button" class="padel-button secondary padel-preview-step" data-direction="next">Next</button>
          <button type="button" class="padel-button padel-preview-confirm" data-analysis-id="${escapeHtml(record.id)}" data-selected-candidate-id="" disabled>Confirm player</button>
        </div>
        <div class="padel-preview-timeline">
          <input type="range" min="0" max="${Math.max(0, frames.length - 1)}" value="${Math.max(0, frames.findIndex((frame) => Number(frame.frame_index) === selectedFrameId))}" class="padel-preview-seek">
          <div class="padel-preview-thumbs">
            ${frames.map((frame, index) => `
              <button
                type="button"
                class="padel-preview-thumb ${Number(frame.frame_index) === selectedFrameId ? 'is-selected' : ''}"
                data-preview-frame-index="${escapeHtml(frame.frame_index)}"
                data-analysis-id="${escapeHtml(record.id)}"
                title="${escapeHtml(`${formatTimestamp(frame.timestamp_ms)} · ${frame.candidates?.length || 0} candidates`)}"
              >
                <span>${escapeHtml(formatTimestamp(frame.timestamp_ms))}</span>
                <strong>${escapeHtml(String(frame.candidates?.length || 0))}</strong>
              </button>
            `).join('')}
          </div>
        </div>
        <div class="padel-match-preview" data-preview-width="${escapeHtml(width)}" data-preview-height="${escapeHtml(height)}">
          <img
            class="padel-match-preview-image"
            src="${escapeHtml(selectedFrameArtifactUrl)}"
            data-artifact-url="${escapeHtml(selectedFrameArtifactUrl)}"
            width="${escapeHtml(width)}"
            height="${escapeHtml(height)}"
            alt="Match preview"
          >
          <div class="padel-match-preview-overlay">
            ${renderPreviewOverlay(record, selectedFrame, selectionRequired, width, height)}
          </div>
        </div>
        <p class="padel-help-note">${escapeHtml(allWarnings.length ? allWarnings.join(' · ') : 'Preview is stored privately and is visible only inside WordPress.')}</p>
      </section>
    `;
  }

  function loadProtectedMedia(element) {
    if (!element || element.tagName === 'IMG' || element.dataset.loaded === '1' || element.getAttribute('src')) return;
    const artifactUrl = element.dataset.artifactUrl;
    if (!artifactUrl) return;

    element.dataset.loaded = '1';
    const fallbackToBlob = async () => {
      try {
        const response = await fetch(artifactUrl, {
          credentials: 'same-origin',
          headers: headers(),
        });
        if (!response.ok) {
          throw new Error(config.strings?.artifactLoadError || 'Unable to load the private artifact.');
        }
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        element.src = objectUrl;
        element.dataset.objectUrl = objectUrl;
        if (typeof element.load === 'function') {
          element.load();
        }
      } catch (error) {
        console.error(error);
        element.dataset.loaded = '0';
      }
    };

    const directLoadFallback = () => {
      if (element.dataset.fallbackAttempted === '1') {
        return;
      }
      element.dataset.fallbackAttempted = '1';
      fallbackToBlob();
    };

    element.addEventListener('error', directLoadFallback, { once: true });
    element.src = artifactUrl;
    if (typeof element.load === 'function') {
      element.load();
    }
  }

  function summaryCard(label, value) {
    return `
      <div class="padel-summary-card">
        <span>${label}</span>
        <strong>${value}</strong>
      </div>
    `;
  }

  function renderSummary(record, match) {
    const selected = match?.selected_player_label || record.selected_player_candidate_id || '—';
    const tracking = match?.tracking || {};
    return `
      <section class="padel-result-section">
        <h4>Match summary</h4>
        <div class="padel-summary-grid">
          ${summaryCard('Status', escapeHtml(record.status_label || record.status || ''))}
          ${summaryCard('Selected player', escapeHtml(selected))}
          ${summaryCard('Tracking coverage', `${formatNumber(tracking.coverage ?? null, 2)}`)}
          ${summaryCard('Tracking confidence', escapeHtml(tracking.confidence_level || '—'))}
          ${summaryCard('Stroke candidates', escapeHtml((match?.stroke_candidates || []).length))}
          ${summaryCard('Processing time', escapeHtml(record.completed_at ? `${formatTimestamp(Date.parse(record.completed_at) - Date.parse(record.created_at || record.completed_at))}` : '—'))}
        </div>
      </section>
    `;
  }

  function renderTracking(record, match) {
    const tracking = match?.tracking || null;
    if (!tracking) return '';

    const intervals = Array.isArray(tracking.missing_intervals) ? tracking.missing_intervals : [];
    return `
      <section class="padel-result-section">
        <h4>Tracking</h4>
        <div class="padel-metric-grid compact">
          <div class="padel-stat"><span>Track ID</span><strong>${escapeHtml(tracking.track_id || '—')}</strong></div>
          <div class="padel-stat"><span>Coverage</span><strong>${formatNumber(tracking.coverage ?? null, 2)}</strong></div>
          <div class="padel-stat"><span>Confidence</span><strong>${escapeHtml(tracking.confidence_level || '—')}</strong></div>
          <div class="padel-stat"><span>Tracked frames</span><strong>${escapeHtml(tracking.tracked_frames ?? 0)} / ${escapeHtml(tracking.total_frames ?? 0)}</strong></div>
        </div>
        ${intervals.length ? `<p class="padel-help-note">${escapeHtml(intervals.map((interval) => `${interval.start_frame}-${interval.end_frame}${interval.reason ? ` (${interval.reason})` : ''}`).join(' · '))}</p>` : ''}
      </section>
    `;
  }

  function renderCandidateCard(record, candidate) {
    const clipUrl = buildArtifactUrl(record.id, `${candidate.candidate_id}-clip`);
    const thumbnailUrl = buildArtifactUrl(record.id, `${candidate.candidate_id}-thumbnail`);
    const reviews = record.manual_reviews || {};
    const review = reviews[candidate.candidate_id] || null;
    const selectedClass = review ? ` is-${escapeHtml(review.review_label)}` : '';

    return `
      <article class="padel-match-candidate-card${selectedClass}">
        <div class="padel-section-head">
          <h5>${escapeHtml(candidate.candidate_id)}</h5>
          <span>${formatNumber(candidate.confidence ?? null, 2)}</span>
        </div>
        <div class="padel-match-candidate-media">
          <img class="padel-match-thumbnail" src="${escapeHtml(thumbnailUrl)}" data-artifact-url="${escapeHtml(thumbnailUrl)}" alt="${escapeHtml(candidate.candidate_id)} thumbnail">
          <video class="padel-protected-video padel-match-clip" controls preload="none" playsinline src="${escapeHtml(clipUrl)}" data-artifact-url="${escapeHtml(clipUrl)}"></video>
        </div>
        <div class="padel-match-candidate-meta">
          <span>Start ${formatTimestamp(candidate.start_timestamp_ms)}</span>
          <span>Peak ${formatTimestamp(candidate.peak_timestamp_ms)}</span>
          <span>End ${formatTimestamp(candidate.end_timestamp_ms)}</span>
        </div>
        <p class="padel-help-note">${escapeHtml((candidate.evidence || []).join(' · ') || '—')}</p>
        <div class="padel-review-actions">
          ${['real', 'false', 'missed', 'unclear'].map((label) => `<button type="button" class="padel-button secondary padel-review-candidate" data-analysis-id="${escapeHtml(record.id)}" data-candidate-id="${escapeHtml(candidate.candidate_id)}" data-review-label="${escapeHtml(label)}">${escapeHtml(label)}</button>`).join('')}
        </div>
        ${review ? `<p class="padel-help-note">Saved review: ${escapeHtml(review.review_label)}${review.notes ? ` · ${escapeHtml(review.notes)}` : ''}</p>` : ''}
      </article>
    `;
  }

  function renderCandidates(record, match) {
    const candidates = Array.isArray(match?.stroke_candidates) ? match.stroke_candidates : [];
    if (!candidates.length) return '';

    return `
      <section class="padel-result-section">
        <h4>Stroke candidates</h4>
        <div class="padel-match-candidate-grid">
          ${candidates.map((candidate) => renderCandidateCard(record, candidate)).join('')}
        </div>
      </section>
    `;
  }

  function renderMatchResult(record) {
    const result = record.result || {};
    const match = result.match || null;
    if (!match) return '';

    return `
      <div class="padel-result-detail">
        ${renderSummary(record, match)}
        ${renderPreview(record, match)}
        ${renderTracking(record, match)}
        ${renderCandidates(record, match)}
        <section class="padel-result-section">
          <h4>Result notes</h4>
          <p>${escapeHtml(result.summary || '')}</p>
          ${(result.findings || []).length ? `<ul>${result.findings.map((item) => `<li>${escapeHtml(String(item))}</li>`).join('')}</ul>` : ''}
          ${(result.recommendations || []).length ? `<ul>${result.recommendations.map((item) => `<li>${escapeHtml(String(item))}</li>`).join('')}</ul>` : ''}
        </section>
      </div>
    `;
  }

  function renderRecord(record) {
    const existing = activeWrap?.querySelector(`[data-analysis-id="${record.id}"]`);
    const match = record.result?.match || null;
    const previewFrameIndex = Number(match?.preview?.frame_index || 0);
    const html = `
      <article class="padel-analysis-card padel-match-card" data-analysis-id="${record.id}" data-status="${escapeHtml(record.status || '')}" data-preview-frame-index="${escapeHtml(previewFrameIndex)}" data-record='${escapeHtml(JSON.stringify(record))}'>
        <div class="padel-analysis-head">
          <div>
            <p class="padel-card-kicker">Match #${escapeHtml(record.id)}</p>
            <h3>${escapeHtml(record.title || '')}</h3>
          </div>
          <span class="padel-status-badge is-${escapeHtml(record.status || '')}">${escapeHtml(record.status_label || '')}</span>
        </div>
        <div class="padel-analysis-meta">
          <span>Hand: ${escapeHtml(record.dominant_hand_label || '')}</span>
          <span>Angle: ${escapeHtml(record.camera_angle_label || '')}</span>
          <span>Mode: ${escapeHtml(record.analysis_mode_label || '')}</span>
        </div>
        <div class="padel-progress-wrap small">
          <div class="padel-progress-meta"><span>${escapeHtml(record.status_label || '')}</span><span class="padel-card-progress">${record.progress || 0}%</span></div>
          <div class="padel-progress-bar"><div class="padel-progress-fill" style="width:${record.progress || 0}%"></div></div>
        </div>
        <div class="padel-analysis-submeta">
          <span>${escapeHtml((record.video && record.video.name) || '')}</span>
          <span>${escapeHtml((record.video && record.video.size_human) || '')}</span>
          <span>${escapeHtml(String((record.video && record.video.duration_seconds) || 0))} sec</span>
        </div>
        ${renderMatchResult(record)}
        <div class="padel-actions">
          <button type="button" class="padel-button secondary padel-delete-analysis" data-analysis-id="${record.id}">מחק משחק ורשומה</button>
        </div>
      </article>
    `;

    if (existing) {
      existing.outerHTML = html;
    } else if (activeWrap) {
      activeWrap.prepend(createFragment(html));
      activeWrap.querySelector('.padel-empty')?.remove();
    }

    const card = activeWrap?.querySelector(`[data-analysis-id="${record.id}"]`);
    card?.querySelectorAll('.padel-protected-video').forEach(loadProtectedMedia);

    const matchStage = record.result?.match?.stage || '';
    if (matchStage === 'selection_required') {
      stopPolling(record.id);
    } else if (record.status === 'uploaded' || record.status === 'processing') {
      startPolling(record.id);
    } else {
      stopPolling(record.id);
    }
  }

  function updatePreviewFrame(card, frameIndex) {
    if (!card) return;
    const payload = card.dataset.record;
    if (!payload) return;
    let record;
    try {
      record = JSON.parse(payload);
    } catch (error) {
      console.error(error);
      return;
    }

    const match = record.result?.match || null;
    if (!match) return;
    const previewShell = card.querySelector('.padel-preview-frame-shell');
    if (!previewShell) return;

    card.dataset.previewFrameIndex = String(frameIndex);
    delete card.dataset.selectedCandidateId;
    previewShell.outerHTML = renderPreview(record, match, frameIndex);
    card.querySelectorAll('.padel-protected-video').forEach(loadProtectedMedia);
  }

  function setSelectedPreviewCandidate(card, candidateButton) {
    if (!card || !candidateButton) return;
    const candidateId = candidateButton.dataset.candidateId || '';
    if (!candidateId) return;

    card.dataset.selectedCandidateId = candidateId;
    card.querySelectorAll('.padel-match-box.is-selected').forEach((button) => button.classList.remove('is-selected'));
    candidateButton.classList.add('is-selected');

    const confirmButton = card.querySelector('.padel-preview-confirm');
    if (confirmButton) {
      confirmButton.dataset.selectedCandidateId = candidateId;
      confirmButton.removeAttribute('disabled');
    }
  }

  function confirmSelectedPreviewCandidate(card, button) {
    if (!card) return;
    const candidateId = card.dataset.selectedCandidateId || '';
    if (!candidateId) return;

    if (button) {
      button.setAttribute('disabled', 'disabled');
    }

    selectPlayer(card.dataset.analysisId, candidateId).finally(() => {
      button?.removeAttribute('disabled');
    });
  }

  function createFragment(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html.trim();
    return tpl.content.firstElementChild;
  }

  function startPolling(id) {
    if (pendingPolls.has(id)) return;

    const interval = window.setInterval(async () => {
      try {
        const response = await fetch(`${config.restUrl}/analysis/${id}`, {
          method: 'GET',
          credentials: 'same-origin',
          headers: headers(),
        });
        const data = await response.json();
        if (!response.ok || !data.analysis) {
          throw new Error(data?.message || 'Unable to load analysis status');
        }
        renderRecord(data.analysis);
        const stage = data.analysis?.result?.match?.stage || '';
        if (stage === 'selection_required' || data.analysis.status === 'awaiting_player_selection' || data.analysis.status === 'completed' || data.analysis.status === 'failed') {
          stopPolling(id);
        }
      } catch (error) {
        console.error(error);
      }
    }, 2000);

    pendingPolls.set(id, interval);
  }

  function stopPolling(id) {
    const interval = pendingPolls.get(id);
    if (interval) {
      window.clearInterval(interval);
      pendingPolls.delete(id);
    }
  }

  function submitUpload(event) {
    event.preventDefault();
    if (!form) return;

    const formData = new FormData(form);
    setProgress(12, 'Uploading match video...');
    setMessage('');

    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${config.restUrl}/match-upload`);
    xhr.withCredentials = true;
    xhr.setRequestHeader('X-WP-Nonce', config.nonce || '');

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.min(98, Math.max(12, Math.round((event.loaded / event.total) * 100)));
      setProgress(percent, 'Uploading match video...');
    };

    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText || '{}');
        if (xhr.status < 200 || xhr.status >= 300 || !data.analysis) {
          throw new Error(data?.message || config.strings?.uploadError || 'Upload failed');
        }

        setProgress(data.analysis.progress || 24, data.analysis.status_label || 'Uploaded');
        setMessage(config.strings?.uploadSuccess || 'Match video uploaded successfully.');
        renderRecord(data.analysis);
        form.reset();
        if (data.analysis.status === 'uploaded' || data.analysis.status === 'processing') {
          startPolling(data.analysis.id);
        }
      } catch (error) {
        console.error(error);
        setProgress(0, 'Ready');
        setMessage(error.message || config.strings?.uploadError || 'Upload failed', true);
      }
    };

    xhr.onerror = () => {
      setProgress(0, 'Ready');
      setMessage(config.strings?.uploadError || 'Upload failed', true);
    };

    xhr.send(formData);
  }

  async function deleteAnalysis(analysisId, button) {
    if (!analysisId) return;
    if (!window.confirm(config.strings?.deleteConfirm || 'Delete this analysis?')) return;

    try {
      button?.setAttribute('disabled', 'disabled');
      const response = await fetch(`${config.restUrl}/analysis/${analysisId}`, {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: headers(),
      });

      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data?.message || 'Delete failed');
      }

      stopPolling(analysisId);
      const card = activeWrap?.querySelector(`[data-analysis-id="${analysisId}"]`);
      if (card) card.remove();
      if (activeWrap && !activeWrap.querySelector('.padel-analysis-card')) {
        activeWrap.innerHTML = '<p class="padel-empty">עדיין לא הועלה משחק.</p>';
      }
    } catch (error) {
      console.error(error);
      alert(error.message || 'Delete failed');
    } finally {
      button?.removeAttribute('disabled');
    }
  }

  async function selectPlayer(analysisId, candidateId) {
    if (!analysisId || !candidateId) return;

    try {
      const response = await fetch(`${config.selectionUrlBase.replace(/\/?$/, '/')}${encodeURIComponent(String(analysisId))}/selected-player`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          ...headers(),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ selected_player_candidate_id: candidateId }),
      });

      const data = await response.json();
      if (!response.ok || !data.analysis) {
        throw new Error(data?.message || 'Player selection failed');
      }

      setMessage(config.strings?.selectionSuccess || 'Player selected successfully.');
      renderRecord(data.analysis);
    } catch (error) {
      console.error(error);
      setMessage(error.message || 'Player selection failed', true);
    }
  }

  async function reviewCandidate(analysisId, candidateId, reviewLabel) {
    if (!analysisId || !candidateId || !reviewLabel) return;

    try {
      const response = await fetch(`${config.reviewUrlBase.replace(/\/?$/, '/')}${encodeURIComponent(String(analysisId))}/candidate-review`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          ...headers(),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          candidate_id: candidateId,
          review_label: reviewLabel,
          notes: '',
        }),
      });

      const data = await response.json();
      if (!response.ok || !data.analysis) {
        throw new Error(data?.message || 'Review save failed');
      }

      setMessage(config.strings?.reviewSuccess || 'Review saved.');
      renderRecord(data.analysis);
    } catch (error) {
      console.error(error);
      setMessage(error.message || 'Review save failed', true);
    }
  }

  form?.addEventListener('submit', submitUpload);

  activeWrap?.addEventListener('click', (event) => {
    const deleteButton = event.target.closest('.padel-delete-analysis');
    if (deleteButton) {
      deleteAnalysis(deleteButton.dataset.analysisId, deleteButton);
      return;
    }

    const previewStepButton = event.target.closest('.padel-preview-step');
    if (previewStepButton) {
      const card = previewStepButton.closest('.padel-match-card');
      if (!card) return;
      const payload = card.dataset.record;
      if (!payload) return;
      let record;
      try {
        record = JSON.parse(payload);
      } catch (error) {
        console.error(error);
        return;
      }
      const match = record.result?.match || null;
      const frames = getPreviewFrames(match);
      if (!frames.length) return;
      const currentIndex = Number(card.dataset.previewFrameIndex || match?.preview?.frame_index || frames[0].frame_index || 0);
      const currentPosition = Math.max(0, frames.findIndex((frame) => Number(frame.frame_index) === currentIndex));
      const nextPosition = previewStepButton.dataset.direction === 'prev'
        ? Math.max(0, currentPosition - 1)
        : Math.min(frames.length - 1, currentPosition + 1);
      const nextFrame = frames[nextPosition];
      if (nextFrame) {
        updatePreviewFrame(card, Number(nextFrame.frame_index));
      }
      return;
    }

    const previewThumb = event.target.closest('.padel-preview-thumb');
    if (previewThumb) {
      const card = previewThumb.closest('.padel-match-card');
      if (!card) return;
      updatePreviewFrame(card, Number(previewThumb.dataset.previewFrameIndex || 0));
      return;
    }

    const selectButton = event.target.closest('.padel-match-box.is-selectable');
    if (selectButton) {
      const card = selectButton.closest('.padel-match-card');
      setSelectedPreviewCandidate(card, selectButton);
      return;
    }

    const confirmButton = event.target.closest('.padel-preview-confirm');
    if (confirmButton) {
      const card = confirmButton.closest('.padel-match-card');
      confirmSelectedPreviewCandidate(card, confirmButton);
      return;
    }

    const reviewButton = event.target.closest('.padel-review-candidate');
    if (reviewButton) {
      reviewCandidate(reviewButton.dataset.analysisId, reviewButton.dataset.candidateId, reviewButton.dataset.reviewLabel);
    }
  });

  activeWrap?.addEventListener('change', (event) => {
    const seek = event.target.closest('.padel-preview-seek');
    if (!seek) return;
    const card = seek.closest('.padel-match-card');
    if (!card) return;
    const payload = card.dataset.record;
    if (!payload) return;

    let record;
    try {
      record = JSON.parse(payload);
    } catch (error) {
      console.error(error);
      return;
    }

    const match = record.result?.match || null;
    const frames = getPreviewFrames(match);
    if (!frames.length) return;
    const nextFrame = frames[Math.max(0, Math.min(frames.length - 1, Number(seek.value || 0)))];
    if (nextFrame) {
      updatePreviewFrame(card, Number(nextFrame.frame_index));
    }
  });

  activeWrap?.querySelectorAll('.padel-analysis-card').forEach((card) => {
    const payload = card.dataset.record;
    if (payload) {
      try {
        renderRecord(JSON.parse(payload));
        return;
      } catch (error) {
        console.error(error);
      }
    }

    const status = card.dataset.status;
    const id = card.dataset.analysisId;
    if (status === 'uploaded' || status === 'processing') {
      startPolling(id);
    }
  });
})();
