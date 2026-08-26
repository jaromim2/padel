(() => {
  const config = window.PadelVideoAnalysis || {};
  const form = document.getElementById('padel-video-analysis-form');
  const activeWrap = document.getElementById('padel-active-analysis');
  const uploadState = document.getElementById('padel-upload-state');
  const uploadPercent = document.getElementById('padel-upload-percent');
  const uploadFill = document.getElementById('padel-upload-fill');
  const uploadMessage = document.getElementById('padel-upload-message');
  const pendingPolls = new Map();
  let calibrationSummaryRendered = false;

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

  function formatAngle(value) {
    if (value === null || value === undefined || value === '') return '—';
    return `${formatNumber(value, 1)}°`;
  }

  function formatTimestamp(ms) {
    if (ms === null || ms === undefined || ms === '') return '—';
    const value = Number(ms);
    if (Number.isNaN(value)) return '—';
    return `${formatNumber(value / 1000, 2)}s`;
  }

  function normalizeAngle(value) {
    const numeric = Number(value);
    if (Number.isNaN(numeric)) return null;
    const normalized = ((numeric + 180) % 360) - 180;
    return normalized === -180 && numeric > 0 ? 180 : normalized;
  }

  function shortestAngleDelta(previous, current) {
    const prev = Number(previous);
    const next = Number(current);
    if (Number.isNaN(prev) || Number.isNaN(next)) return null;
    return normalizeAngle(next - prev);
  }

  function buildArtifactUrl(analysisId, artifact) {
    const base = config.artifactUrlBase || '';
    if (base) {
      return `${base.replace(/\/?$/, '/')}${encodeURIComponent(String(analysisId))}/artifact/${encodeURIComponent(String(artifact))}`;
    }
    return `${config.restUrl}/analysis/${analysisId}/artifact/${artifact}`;
  }

  function loadProtectedVideo(video) {
    if (!video || video.dataset.loaded === '1' || video.getAttribute('src')) return;
    const artifactUrl = video.dataset.artifactUrl;
    if (!artifactUrl) return;

    video.dataset.loaded = '1';
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
        video.src = objectUrl;
        video.dataset.objectUrl = objectUrl;
        if (typeof video.load === 'function') {
          video.load();
        }
      } catch (error) {
        console.error(error);
        video.removeAttribute('src');
        video.dataset.loaded = '0';
      }
    };

    const directLoadFallback = () => {
      if (video.dataset.fallbackAttempted === '1') {
        return;
      }
      video.dataset.fallbackAttempted = '1';
      fallbackToBlob();
    };

    video.addEventListener('error', directLoadFallback, { once: true });
    video.src = artifactUrl;
    if (typeof video.load === 'function') {
      video.load();
    }
  }

  function buildContactFrameUrl(analysisId) {
    const base = config.contactFrameUrlBase || '';
    if (base) {
      return `${base.replace(/\/?$/, '/')}${encodeURIComponent(String(analysisId))}/contact-frame`;
    }
    return `${config.restUrl}/analysis/${analysisId}/contact-frame`;
  }

  function renderStatusSummary(record, pose, stroke) {
    const totalMs = getProcessingDurationMs(record);
    return `
      <div class="padel-summary-grid">
        ${summaryCard('סטטוס', escapeHtml(record.status_label || record.status || ''))}
        ${summaryCard('דיוק פוזה', escapeHtml(pose?.confidence_level || '—'))}
        ${summaryCard('כיסוי פוזה', `${formatNumber(pose?.pose_coverage ?? null, 2)}`)}
        ${summaryCard('גוף מלא נראה', pose?.full_body_visible ? 'כן' : 'לא')}
        ${summaryCard('נכון לזרוע הדומיננטית', escapeHtml(stroke?.dominant_hand || record.dominant_hand_label || '—'))}
        ${summaryCard('Stroke available', stroke?.available ? 'כן' : 'לא')}
        ${summaryCard('משך עיבוד', totalMs === null ? '—' : `${formatNumber(totalMs / 1000, 2)}s`)}
      </div>
    `;
  }

  function summaryCard(label, value) {
    return `
      <div class="padel-summary-card">
        <span>${label}</span>
        <strong>${value}</strong>
      </div>
    `;
  }

  function getProcessingDurationMs(record) {
    const created = record.created_at ? Date.parse(record.created_at) : NaN;
    const completed = record.completed_at ? Date.parse(record.completed_at) : NaN;
    if (!Number.isNaN(created) && !Number.isNaN(completed) && completed >= created) {
      return completed - created;
    }
    return null;
  }

  function renderPoseVisibility(pose) {
    const visibility = pose?.visibility || {};
    const rows = [
      ['shoulders', 'כתפיים'],
      ['elbows', 'מרפקים'],
      ['wrists', 'שורש כף יד'],
      ['hips', 'ירכיים'],
      ['knees', 'ברכיים'],
      ['ankles', 'קרסוליים'],
    ];

    return `
      <section class="padel-result-section">
        <h4>זמינות פוזה</h4>
        <div class="padel-metric-grid compact">
          ${rows.map(([key, label]) => metricStat(label, formatNumber(visibility[key] ?? null, 2))).join('')}
        </div>
      </section>
    `;
  }

  function metricStat(label, value) {
    return `
      <div class="padel-stat">
        <span>${label}</span>
        <strong>${value}</strong>
      </div>
    `;
  }

  function renderVideoPlayer(record) {
    const url = buildArtifactUrl(record.id, 'annotated-video');
    return `
      <section class="padel-result-section">
        <div class="padel-section-head">
          <h4>Annotated video</h4>
          <span>Private playback through WordPress</span>
        </div>
        <video class="padel-annotated-video" controls preload="none" playsinline src="${escapeHtml(url)}" data-artifact-url="${escapeHtml(url)}"></video>
        <p class="padel-help-note">הוידאו נגיש רק דרך המסך המאובטח של WordPress.</p>
      </section>
    `;
  }

  function renderPhaseTimeline(record, stroke) {
    const phases = stroke?.phases || {};
    const items = [
      ['ready', 'Ready', phases.ready],
      ['preparation_start', 'Preparation start', phases.preparation_start],
      ['backswing_end', 'Backswing end', phases.backswing_end],
      ['contact_estimate', 'Estimated contact', phases.contact_estimate],
      ['follow_through_peak', 'Follow-through peak', phases.follow_through_peak],
      ['recovery', 'Recovery', phases.recovery],
    ].filter(([, , phase]) => phase);

    if (!items.length) return '';

    return `
      <section class="padel-result-section">
        <div class="padel-section-head">
          <h4>Phase timeline</h4>
          <span>Click a phase to seek the video</span>
        </div>
        <div class="padel-phase-timeline">
          ${items.map(([key, label, phase]) => `
            <button type="button" class="padel-phase-row" data-phase-frame="${escapeHtml(phase.frame_index ?? '')}" data-phase-time="${escapeHtml(phase.timestamp_ms ?? '')}" data-analysis-id="${escapeHtml(record.id)}">
              <span class="phase-label">${escapeHtml(label)}</span>
              <span class="phase-frame">Frame ${escapeHtml(phase.frame_index ?? '—')}</span>
              <span class="phase-time">${formatTimestamp(phase.timestamp_ms)}</span>
              <span class="phase-confidence">${formatNumber(phase.confidence ?? null, 2)}</span>
            </button>
          `).join('')}
        </div>
      </section>
    `;
  }

  function renderContactSection(record, stroke) {
    const phases = stroke?.phases || {};
    const estimate = phases.contact_estimate || null;
    if (!estimate) return '';

    const original = phases.original_contact_estimate || estimate;
    const candidateStart = estimate.candidate_start_frame ?? estimate.frame_index ?? 0;
    const candidateEnd = estimate.candidate_end_frame ?? estimate.frame_index ?? candidateStart;
    const current = Number(record.manual_contact_frame_index ?? estimate.frame_index ?? original.frame_index ?? candidateStart);
    const source = estimate.source || 'estimated';
    const isManual = source === 'manual' || record.manual_contact_frame_source === 'manual';

    return `
      <section class="padel-result-section">
        <div class="padel-section-head">
          <h4>Estimated contact</h4>
          <span>Selected frame source: ${escapeHtml(source)}</span>
        </div>
        <div class="padel-contact-copy">
          <div>
            <strong>Estimated frame</strong>
            <p>${escapeHtml(estimate.frame_index ?? '—')} · ${formatTimestamp(estimate.timestamp_ms)}</p>
          </div>
          <div>
            <strong>Confidence</strong>
            <p>${formatNumber(estimate.confidence ?? null, 2)}</p>
          </div>
          <div>
            <strong>Candidate window</strong>
            <p>${escapeHtml(candidateStart)} - ${escapeHtml(candidateEnd)}</p>
          </div>
          <div>
            <strong>Evidence</strong>
            <p>${escapeHtml((estimate.evidence || []).join(' · ') || '—')}</p>
          </div>
        </div>
        ${original && original.frame_index !== estimate.frame_index ? `
          <p class="padel-original-contact">Original estimate: frame ${escapeHtml(original.frame_index)} · ${formatTimestamp(original.timestamp_ms)}</p>
        ` : ''}
        <div class="padel-contact-editor" data-analysis-id="${escapeHtml(record.id)}" data-candidate-start="${escapeHtml(candidateStart)}" data-candidate-end="${escapeHtml(candidateEnd)}" data-current-frame="${escapeHtml(current)}">
          <label>
            <span>בחר frame</span>
            <input type="range" class="padel-contact-range" min="${escapeHtml(candidateStart)}" max="${escapeHtml(candidateEnd)}" value="${escapeHtml(current)}">
          </label>
          <div class="padel-contact-stepper">
            <button type="button" class="padel-button secondary padel-contact-step" data-step="-1">Frame -</button>
            <button type="button" class="padel-button secondary padel-contact-step" data-step="1">Frame +</button>
            <button type="button" class="padel-button padel-contact-confirm">Use this contact frame</button>
          </div>
          <p class="padel-contact-preview">Selected frame: <strong class="padel-contact-selected-frame">${escapeHtml(current)}</strong> · <span class="padel-contact-selected-time">${formatTimestamp(estimateTimestampForFrame(record, current, estimate))}</span>${isManual ? ' · manual override' : ''}</p>
        </div>
      </section>
    `;
  }

  function estimateTimestampForFrame(record, frame, fallback) {
    const phases = record?.result?.stroke?.phases || {};
    const estimate = phases.contact_estimate || fallback || {};
    const allPhases = [phases.ready, phases.preparation_start, phases.backswing_end, phases.contact_estimate, phases.follow_through_peak, phases.recovery].filter(Boolean);
    const match = allPhases.find((item) => Number(item.frame_index) === Number(frame));
    if (match) return match.timestamp_ms ?? null;

    const totalFrames = Number(record?.result?.pose?.total_frames || 0);
    const durationSeconds = Number(record?.video?.duration_seconds || 0);
    if (totalFrames > 1 && durationSeconds > 0) {
      const timestamp = (Number(frame) / Math.max(1, totalFrames - 1)) * durationSeconds * 1000;
      return Math.round(timestamp);
    }

    return estimate.timestamp_ms ?? null;
  }

  function renderMetrics(record, stroke) {
    if (!stroke) return '';
    const metrics = stroke.metrics || {};
    const elbow = metrics.dominant_elbow_angle || {};
    const shoulder = metrics.shoulder_line_orientation || {};
    const hip = metrics.hip_line_orientation || {};
    const knees = metrics.knee_angles_at_contact || {};
    const wrist = metrics.wrist_velocity_profile || {};
    const balance = metrics.balance_proxy || {};

    return `
      <section class="padel-result-section">
        <h4>Movement metrics</h4>
        <div class="padel-metric-grid">
          ${metricPhaseTriple('Elbow angle', elbow)}
          ${metricPhaseTriple('Shoulder-line orientation', shoulder, true)}
          ${metricPhaseTriple('Hip-line orientation', hip, true)}
          ${metricKnees('Knee angles at contact', knees)}
          ${metricWrist('Wrist velocity peak', wrist)}
          ${metricBalance('Balance proxy', balance, metrics.recovery_duration_ms)}
        </div>
      </section>
    `;
  }

  function confidenceLabel(confidence, kind = '') {
    if (kind === 'neutral_measurement') {
      return 'מדידה ניטרלית';
    }
    switch (confidence) {
      case 'high':
        return 'ממצא מבוסס';
      case 'medium':
        return 'ממצא סביר';
      case 'low':
        return 'צפייה אפשרית';
      default:
        return 'אין מספיק מידע';
    }
  }

  function metricPhaseTriple(title, triple, orientation = false) {
    const rows = [
      ['Preparation', triple.preparation],
      ['Contact', triple.contact],
      ['Follow-through', triple.follow_through],
    ];
    return `
      <article class="padel-metric-card">
        <h5>${title}</h5>
        ${rows.map(([label, measurement]) => renderMeasurementRow(label, measurement, orientation)).join('')}
      </article>
    `;
  }

  function renderMeasurementRow(label, measurement, orientation = false) {
    if (!measurement) {
      return `<div class="padel-measure-row unavailable"><span>${label}</span><strong>—</strong></div>`;
    }
    const value = orientation ? formatAngle(measurement.value_degrees) : formatAngle(measurement.value_degrees);
    const raw = measurement.raw_value_degrees !== null && measurement.raw_value_degrees !== undefined
      ? `Raw ${formatAngle(measurement.raw_value_degrees)}`
      : '';
    const delta = orientation && measurement.raw_value_degrees !== null && measurement.raw_value_degrees !== undefined
      ? `Δ ${formatAngle(shortestAngleDelta(measurement.raw_value_degrees, measurement.value_degrees ?? measurement.raw_value_degrees))}`
      : '';

    return `
      <div class="padel-measure-row">
        <span>${label}</span>
        <strong>${value}</strong>
        <small>${[raw, delta].filter(Boolean).join(' · ')}</small>
      </div>
    `;
  }

  function metricKnees(title, knees) {
    return `
      <article class="padel-metric-card">
        <h5>${title}</h5>
        ${renderMeasurementRow('Left', knees.left)}
        ${renderMeasurementRow('Right', knees.right)}
      </article>
    `;
  }

  function metricWrist(title, wrist) {
    return `
      <article class="padel-metric-card">
        <h5>${title}</h5>
        <div class="padel-measure-row"><span>Peak velocity</span><strong>${formatNumber(wrist.peak_normalized_velocity ?? null, 4)}</strong><small>Frame ${escapeHtml(wrist.peak_frame_index ?? '—')} · ${formatTimestamp(wrist.peak_timestamp_ms)}</small></div>
        <div class="padel-measure-row"><span>Velocity at contact</span><strong>${formatNumber(wrist.velocity_at_contact ?? null, 4)}</strong><small>${wrist.reliable ? 'Reliable' : 'Low confidence'}</small></div>
        <div class="padel-measure-row"><span>Peak acceleration</span><strong>${formatNumber(wrist.peak_acceleration ?? null, 4)}</strong><small>Frame ${escapeHtml(wrist.peak_acceleration_frame_index ?? '—')}</small></div>
      </article>
    `;
  }

  function metricBalance(title, balance, recoveryDurationMs) {
    return `
      <article class="padel-metric-card">
        <h5>${title}</h5>
        <div class="padel-measure-row"><span>Proxy score</span><strong>${formatNumber(balance.proxy_score ?? null, 4)}</strong><small>${balance.reliable ? 'Reliable' : 'Unreliable'}</small></div>
        <div class="padel-measure-row"><span>Body-center drift</span><strong>${formatNumber(balance.body_center_drift ?? null, 4)}</strong><small>${escapeHtml((balance.reasons || []).join(' · ') || '')}</small></div>
        <div class="padel-measure-row"><span>Foot support range</span><strong>${formatNumber(balance.foot_support_range ?? null, 4)}</strong><small></small></div>
        <div class="padel-measure-row"><span>Recovery duration</span><strong>${balance.reasons && balance.reasons.includes('insufficient_post_follow_through_footage') ? 'Unavailable' : `${formatNumber(recoveryDurationMs ?? null, 0)} ms`}</strong><small>${escapeHtml((balance.reasons || []).join(' · ') || '')}</small></div>
      </article>
    `;
  }

  function renderTechnicalDetails(record) {
    const result = record.result || {};
    return `
      <details class="padel-technical-details">
        <summary>Technical details</summary>
        <pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre>
      </details>
    `;
  }

  function renderTechnicalFeedback(record) {
    const feedback = record.result?.technical_feedback;
    if (!feedback) return '';

    const findings = Array.isArray(feedback.findings) ? feedback.findings : [];
    const possibleObservations = Array.isArray(feedback.possible_observations) ? feedback.possible_observations : [];
    const neutralMeasurements = Array.isArray(feedback.neutral_measurements) ? feedback.neutral_measurements : [];
    const withheld = Array.isArray(feedback.withheld_findings) ? feedback.withheld_findings : [];

    return `
      <section class="padel-result-section padel-technical-feedback">
        <div class="padel-section-head">
          <h4>Technical feedback</h4>
          <span>Rules ${escapeHtml(feedback.rules_version || '—')}</span>
        </div>
        <p class="padel-feedback-summary">${feedback.available ? 'המשוב הטכני מבוסס על מדידות זמינות בלבד.' : 'אין עדיין מספיק מידע להפקת ממצאי טכניקה.'}</p>
        <div class="padel-feedback-list">
          ${findings.map((finding) => renderTechnicalFinding(record, finding)).join('')}
        </div>
        ${possibleObservations.length ? `
          <details class="padel-feedback-observations" open>
            <summary>Possible observations requiring clearer footage</summary>
            <div class="padel-feedback-list">
              ${possibleObservations.map((finding) => renderTechnicalFinding(record, finding)).join('')}
            </div>
          </details>
        ` : ''}
        ${neutralMeasurements.length ? `
          <details class="padel-feedback-observations" open>
            <summary>Neutral measurements</summary>
            <div class="padel-feedback-list">
              ${neutralMeasurements.map((finding) => renderTechnicalFinding(record, finding)).join('')}
            </div>
          </details>
        ` : ''}
        ${withheld.length ? `
          <details class="padel-feedback-withheld">
            <summary>ממצאים שנעצרו מחוסר מידע</summary>
            <ul>
              ${withheld.map((item) => `
                <li>
                  <strong>${escapeHtml(item.title || item.rule_id || '')}</strong>
                  <span>${escapeHtml(item.reason || '')}</span>
                  ${Array.isArray(item.missing_capabilities) && item.missing_capabilities.length ? `<small>${escapeHtml(item.missing_capabilities.join(' · '))}</small>` : ''}
                </li>
              `).join('')}
            </ul>
          </details>
        ` : ''}
        ${config.reviewerMode ? renderReviewerFeedback(record, feedback) : ''}
      </section>
    `;
  }

  function reviewLabelOptions(kind) {
    if (kind === 'neutral_measurement') {
      return [
        ['visually_reasonable', 'Measurement appears visually reasonable'],
        ['incorrect', 'Measurement appears incorrect'],
        ['cannot_verify', 'Cannot verify visually'],
      ];
    }

    if (kind === 'withheld_finding') {
      return [
        ['correct', 'Correctly withheld'],
        ['should_not_have_been_withheld', 'Should not have been withheld'],
        ['unclear', 'Unclear'],
      ];
    }

    return [
      ['correct', 'Correct'],
      ['partially_correct', 'Partially correct'],
      ['incorrect', 'Incorrect'],
      ['unclear', 'Unclear'],
    ];
  }

  function renderReviewerFeedback(record, feedback) {
    const review = feedback.review || record.review || {};
    const items = [
      ...(Array.isArray(feedback.findings) ? feedback.findings.map((item) => ({ ...item, item_kind: 'finding' })) : []),
      ...(Array.isArray(feedback.possible_observations) ? feedback.possible_observations.map((item) => ({ ...item, item_kind: 'possible_observation' })) : []),
      ...(Array.isArray(feedback.neutral_measurements) ? feedback.neutral_measurements.map((item) => ({ ...item, item_kind: 'neutral_measurement' })) : []),
      ...(Array.isArray(feedback.withheld_findings) ? feedback.withheld_findings.map((item) => ({ ...item, item_kind: 'withheld_finding' })) : []),
    ];
    const reviewItems = Array.isArray(review.items) ? review.items : [];
    const reviewMap = new Map(reviewItems.map((item) => [`${item.item_kind || ''}:${item.rule_id || ''}`, item]));
    const manualMissed = reviewItems.filter((item) => (item.item_kind || '') === 'manual_missed_finding');
    const ruleCatalog = Array.isArray(config.reviewRuleCatalog?.rules) ? config.reviewRuleCatalog.rules : [];

    return `
      <details class="padel-review-panel" open data-analysis-id="${escapeHtml(record.id)}">
        <summary>Reviewer controls</summary>
        <form class="padel-review-form" data-analysis-id="${escapeHtml(record.id)}" data-rules-version="${escapeHtml(review.rules_version || feedback.rules_version || '')}">
          <div class="padel-review-grid">
            <label>
              <span>Overall video suitability</span>
              <select name="overall_video_suitability">
                ${['excellent', 'usable', 'limited', 'unusable'].map((value) => `<option value="${escapeHtml(value)}" ${review.overall_video_suitability === value ? 'selected' : ''}>${escapeHtml(value)}</option>`).join('')}
              </select>
            </label>
            <label>
              <span>Overall analysis usefulness</span>
              <select name="overall_analysis_usefulness">
                ${['useful', 'partly useful', 'misleading', 'insufficient'].map((value) => `<option value="${escapeHtml(value)}" ${review.overall_analysis_usefulness === value ? 'selected' : ''}>${escapeHtml(value)}</option>`).join('')}
              </select>
            </label>
          </div>
          <p class="padel-help-note">Automatic result checksum: ${escapeHtml(review.automatic_result_checksum || '—')}</p>
          <div class="padel-review-item-list">
            ${items.map((item) => renderReviewItemEditor(item, reviewMap.get(`${item.item_kind}:${item.rule_id}`) || null)).join('')}
          </div>
          <section class="padel-review-manual">
            <div class="padel-section-head">
              <h4>Manual missed findings</h4>
              <button type="button" class="padel-button secondary padel-review-add-manual" data-analysis-id="${escapeHtml(record.id)}">Add manual finding</button>
            </div>
            <div class="padel-review-manual-list">
              ${manualMissed.length ? manualMissed.map((item) => renderManualMissedFindingRow(item, ruleCatalog)).join('') : renderManualMissedFindingRow(null, ruleCatalog)}
            </div>
          </section>
          <div class="padel-review-actions">
            <button type="button" class="padel-button padel-review-save" data-analysis-id="${escapeHtml(record.id)}">Save review</button>
          </div>
        </form>
      </details>
    `;
  }

  function renderReviewItemEditor(item, saved) {
    const options = reviewLabelOptions(item.item_kind);
    const note = saved?.reviewer_note || '';
    const reviewLabel = saved?.review_label || (item.item_kind === 'neutral_measurement' ? 'visually_reasonable' : 'correct');
    const severity = saved?.severity || '';
    const timestampCorrect = saved?.timestamp_correct ? 'checked' : '';
    const wordingCorrect = saved?.wording_correct ? 'checked' : '';
    const shouldHaveBeenWithheld = saved?.should_have_been_withheld ? 'checked' : '';
    const measurementReasonable = saved?.measurement_reasonable ? 'checked' : '';
    const helpLabel = item.item_kind === 'neutral_measurement' ? 'Neutral measurement' : (item.item_kind === 'withheld_finding' ? 'Withheld finding' : 'Automatic finding');

    return `
      <article class="padel-review-item" data-item-kind="${escapeHtml(item.item_kind)}" data-rule-id="${escapeHtml(item.rule_id || '')}" data-supporting-timestamp-ms="${escapeHtml(item.primary_timestamp_ms ?? item.supporting_timestamp_ms ?? '')}" data-supporting-frame-index="${escapeHtml(item.primary_frame_index ?? item.supporting_frame_index ?? '')}">
        <div class="padel-review-item-head">
          <div>
            <strong>${escapeHtml(item.title || item.rule_id || '')}</strong>
            <span>${escapeHtml(helpLabel)}</span>
          </div>
          <span class="padel-review-item-meta">${escapeHtml(item.rule_id || '')}</span>
        </div>
        <div class="padel-review-grid compact">
          <label>
            <span>Review label</span>
            <select name="review_label">
              ${options.map(([value, label]) => `<option value="${escapeHtml(value)}" ${reviewLabel === value ? 'selected' : ''}>${escapeHtml(label)}</option>`).join('')}
            </select>
          </label>
          <label>
            <span>Severity</span>
            <select name="severity">
              ${['minor', 'moderate', 'major'].map((value) => `<option value="${escapeHtml(value)}" ${severity === value ? 'selected' : ''}>${escapeHtml(value)}</option>`).join('')}
            </select>
          </label>
        </div>
        <label>
          <span>Reviewer note</span>
          <textarea name="reviewer_note" rows="3">${escapeHtml(note)}</textarea>
        </label>
        <div class="padel-review-flags">
          <label><input type="checkbox" name="timestamp_correct" ${timestampCorrect}> Timestamp correct</label>
          <label><input type="checkbox" name="wording_correct" ${wordingCorrect}> Wording correct</label>
          <label><input type="checkbox" name="should_have_been_withheld" ${shouldHaveBeenWithheld}> Should have been withheld</label>
          <label><input type="checkbox" name="measurement_reasonable" ${measurementReasonable}> Measurement reasonable</label>
        </div>
        <div class="padel-review-evidence">
          <span>Frame ${escapeHtml(item.primary_frame_index ?? item.supporting_frame_index ?? '—')}</span>
          <span>${escapeHtml(formatTimestamp(item.primary_timestamp_ms ?? item.supporting_timestamp_ms ?? null))}</span>
        </div>
      </article>
    `;
  }

  function renderManualMissedFindingRow(saved, ruleCatalog) {
    const ruleId = saved?.manual_rule_id || saved?.rule_id || '';
    const note = saved?.reviewer_note || '';
    const confidence = saved?.confidence_level || 'low';
    const timestamp = saved?.supporting_timestamp_ms ?? '';
    const frameIndex = saved?.supporting_frame_index ?? '';
    const kind = saved?.manual_miss_kind || 'missed_completely';

    return `
      <article class="padel-review-manual-row" data-manual-row="1">
        <div class="padel-review-grid compact">
          <label>
            <span>Manual rule ID</span>
            <select name="manual_rule_id">
              <option value="">Select a rule</option>
              ${ruleCatalog.map((rule) => `<option value="${escapeHtml(rule.id)}" ${ruleId === rule.id ? 'selected' : ''}>${escapeHtml(rule.title || rule.id)}</option>`).join('')}
            </select>
          </label>
          <label>
            <span>Confidence</span>
            <select name="confidence_level">
              ${['high', 'medium', 'low'].map((value) => `<option value="${escapeHtml(value)}" ${confidence === value ? 'selected' : ''}>${escapeHtml(value)}</option>`).join('')}
            </select>
          </label>
          <label>
            <span>Manual finding type</span>
            <select name="manual_miss_kind">
              ${['missed_completely', 'misclassified'].map((value) => `<option value="${escapeHtml(value)}" ${kind === value ? 'selected' : ''}>${escapeHtml(value)}</option>`).join('')}
            </select>
          </label>
        </div>
        <div class="padel-review-grid compact">
          <label>
            <span>Supporting timestamp ms</span>
            <input type="number" name="supporting_timestamp_ms" min="0" step="1" value="${escapeHtml(timestamp)}">
          </label>
          <label>
            <span>Supporting frame</span>
            <input type="number" name="supporting_frame_index" min="0" step="1" value="${escapeHtml(frameIndex)}">
          </label>
        </div>
        <label>
          <span>Reviewer note</span>
          <textarea name="reviewer_note" rows="3">${escapeHtml(note)}</textarea>
        </label>
      </article>
    `;
  }

  function renderTechnicalFinding(record, finding) {
    const evidence = Array.isArray(finding.evidence) ? finding.evidence : [];
    const phases = Array.isArray(finding.phase_references) ? finding.phase_references : [];
    const seekTimestamp = resolveFeedbackSeekTimestamp(record, finding);
    const evidenceText = evidence.length
      ? evidence.map((item) => {
        const label = item.label || 'evidence';
        const value = item.value === null || item.value === undefined ? '' : `: ${item.value}`;
        const time = item.timestamp_ms !== null && item.timestamp_ms !== undefined ? ` (${formatTimestamp(item.timestamp_ms)})` : '';
        return `<button type="button" class="padel-feedback-evidence-chip" data-analysis-id="${escapeHtml(record.id)}" data-feedback-seek-timestamp="${escapeHtml(item.timestamp_ms ?? '')}">${escapeHtml(label)}${escapeHtml(value)}${escapeHtml(time)}</button>`;
      }).join('')
      : '<span class="padel-feedback-muted">אין evidence מפורש</span>';

    return `
      <article class="padel-feedback-card is-${escapeHtml(finding.confidence_level || 'low')}">
        <div class="padel-feedback-head">
          <div>
            <h5>${escapeHtml(finding.title || finding.rule_id || '')}</h5>
            <p>${escapeHtml(finding.observation || '')}</p>
          </div>
          <div class="padel-feedback-badges">
            <span class="padel-feedback-confidence">${escapeHtml(confidenceLabel(finding.confidence_level, finding.kind))}</span>
            <span class="padel-feedback-importance">${escapeHtml(finding.importance || '')}</span>
          </div>
        </div>
        <div class="padel-feedback-meta">
          <span>Measure: ${escapeHtml(finding.metric_name || '—')}</span>
          <span>Value: ${formatNumber(finding.measured_value ?? null, 2)} ${escapeHtml(finding.unit || '')}</span>
          <span>Threshold: ${formatNumber(finding.threshold_value ?? null, 2)} ${escapeHtml(finding.unit || '')}</span>
          <span>Frame: ${escapeHtml(finding.primary_frame_index ?? '—')} · ${formatTimestamp(finding.primary_timestamp_ms ?? null)}</span>
        </div>
        ${phases.length ? `<div class="padel-feedback-phases">${phases.map((phase) => `<button type="button" class="padel-feedback-phase-chip" data-analysis-id="${escapeHtml(record.id)}" data-feedback-seek-timestamp="${escapeHtml(phase.timestamp_ms ?? '')}">${escapeHtml(phase.phase || '')}</button>`).join('')}</div>` : ''}
        <div class="padel-feedback-evidence">${evidenceText}</div>
        ${Array.isArray(finding.limitations) && finding.limitations.length ? `<p class="padel-feedback-limitations">${escapeHtml(finding.limitations.join(' · '))}</p>` : ''}
        <div class="padel-feedback-actions">
          <button type="button" class="padel-button secondary padel-feedback-jump" data-analysis-id="${escapeHtml(record.id)}" data-feedback-seek-timestamp="${escapeHtml(seekTimestamp ?? '')}">Jump to support frame</button>
        </div>
      </article>
    `;
  }

  function renderCalibrationSummary() {
    if (!config.reviewerMode) return '';
    if (calibrationSummaryRendered) return '';
    const summary = config.calibrationSummary || {};
    const rows = Array.isArray(summary.rule_rows) ? summary.rule_rows : [];
    if (!rows.length) {
      return '';
    }

    calibrationSummaryRendered = true;

    return `
      <section class="padel-result-section padel-calibration-summary">
        <div class="padel-section-head">
          <h4>Calibration summary</h4>
          <span>Rules ${escapeHtml(summary.rules_version || config.reviewRuleCatalog?.version || '—')}</span>
        </div>
        <div class="padel-review-summary-table">
          <div class="padel-review-summary-head">
            <span>Rule</span>
            <span>Evaluated</span>
            <span>Correct</span>
            <span>Partial</span>
            <span>Incorrect</span>
            <span>Unclear</span>
            <span>Precision</span>
            <span>Average confidence</span>
          </div>
          ${rows.map((row) => `
            <div class="padel-review-summary-row">
              <strong>${escapeHtml(row.rule_id || '')}</strong>
              <span>${escapeHtml(row.evaluated_count ?? 0)}</span>
              <span>${escapeHtml(row.correct_count ?? 0)}</span>
              <span>${escapeHtml(row.partially_correct_count ?? 0)}</span>
              <span>${escapeHtml(row.incorrect_count ?? 0)}</span>
              <span>${escapeHtml(row.unclear_count ?? 0)}</span>
              <span>${escapeHtml(formatNumber(row.evaluated_count ? Number(row.correct_count || 0) / Number(row.evaluated_count || 1) : 0, 2))}</span>
              <span>${escapeHtml(formatNumber(row.average_confidence ?? null, 2))}</span>
            </div>
          `).join('')}
        </div>
      </section>
    `;
  }

  function serializeReviewPayload(card) {
    const form = card?.querySelector('.padel-review-form');
    if (!form) return null;

    const payload = {
      rules_version: form.dataset.rulesVersion || '',
      overall_video_suitability: form.querySelector('[name="overall_video_suitability"]')?.value || '',
      overall_analysis_usefulness: form.querySelector('[name="overall_analysis_usefulness"]')?.value || '',
      items: [],
      manual_missed_findings: [],
    };

    form.querySelectorAll('.padel-review-item').forEach((item) => {
      const kind = item.dataset.itemKind || '';
      const ruleId = item.dataset.ruleId || '';
      if (!kind || !ruleId) return;
      payload.items.push({
        item_kind: kind,
        rule_id: ruleId,
        title: item.querySelector('.padel-review-item-head strong')?.textContent || ruleId,
        review_label: item.querySelector('[name="review_label"]')?.value || '',
        severity: item.querySelector('[name="severity"]')?.value || '',
        reviewer_note: item.querySelector('[name="reviewer_note"]')?.value || '',
        timestamp_correct: item.querySelector('[name="timestamp_correct"]')?.checked || false,
        wording_correct: item.querySelector('[name="wording_correct"]')?.checked || false,
        should_have_been_withheld: item.querySelector('[name="should_have_been_withheld"]')?.checked || false,
        measurement_reasonable: item.querySelector('[name="measurement_reasonable"]')?.checked || false,
        supporting_timestamp_ms: item.dataset.supportingTimestampMs || '',
        supporting_frame_index: item.dataset.supportingFrameIndex || '',
      });
    });

    form.querySelectorAll('.padel-review-manual-row').forEach((row) => {
      const manualRuleId = row.querySelector('[name="manual_rule_id"]')?.value || '';
      if (!manualRuleId) return;
      payload.manual_missed_findings.push({
        manual_rule_id: manualRuleId,
        confidence_level: row.querySelector('[name="confidence_level"]')?.value || '',
        manual_miss_kind: row.querySelector('[name="manual_miss_kind"]')?.value || '',
        supporting_timestamp_ms: row.querySelector('[name="supporting_timestamp_ms"]')?.value || '',
        supporting_frame_index: row.querySelector('[name="supporting_frame_index"]')?.value || '',
        reviewer_note: row.querySelector('[name="reviewer_note"]')?.value || '',
      });
    });

    return payload;
  }

  function addManualMissedFindingRow(card) {
    const list = card?.querySelector('.padel-review-manual-list');
    const catalog = Array.isArray(config.reviewRuleCatalog?.rules) ? config.reviewRuleCatalog.rules : [];
    if (!list) return;
    const tpl = document.createElement('template');
    tpl.innerHTML = renderManualMissedFindingRow(null, catalog).trim();
    const row = tpl.content.firstElementChild;
    if (row) list.appendChild(row);
  }

  async function saveReview(card, button) {
    const analysisId = card?.dataset.analysisId;
    if (!analysisId) return;
    const payload = serializeReviewPayload(card);
    if (!payload) return;

    try {
      button?.setAttribute('disabled', 'disabled');
      const response = await fetch(`${config.reviewUrlBase.replace(/\/?$/, '/')}${encodeURIComponent(String(analysisId))}/review`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          ...headers(),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok || !data.analysis) {
        throw new Error(data?.message || config.strings?.reviewSaveFailed || 'Unable to save review.');
      }
      renderRecord(data.analysis);
      setMessage(config.strings?.reviewSaved || 'Review saved successfully.');
    } catch (error) {
      console.error(error);
      setMessage(error.message || config.strings?.reviewSaveFailed || 'Unable to save review.', true);
    } finally {
      button?.removeAttribute('disabled');
    }
  }

  function resolveFeedbackSeekTimestamp(record, finding) {
    if (finding?.primary_timestamp_ms !== null && finding?.primary_timestamp_ms !== undefined) {
      return finding.primary_timestamp_ms;
    }
    const evidence = Array.isArray(finding?.evidence) ? finding.evidence : [];
    const firstEvidence = evidence.find((item) => item && item.timestamp_ms !== null && item.timestamp_ms !== undefined);
    if (firstEvidence) return firstEvidence.timestamp_ms;
    const phases = Array.isArray(finding?.phase_references) ? finding.phase_references : [];
    const firstPhase = phases.find((item) => item && item.timestamp_ms !== null && item.timestamp_ms !== undefined);
    if (firstPhase) return firstPhase.timestamp_ms;
    return null;
  }

  function renderResult(record) {
    const result = record.result || {};
    const pose = result.pose || null;
    const stroke = result.stroke || null;

    if (!pose && !stroke) {
      return '<div class="padel-result"><strong>תוצאת דמה</strong><p>התהליך הושלם, אך לא הוחזר מבנה פוזה/סטורק מפורט.</p></div>';
    }

    return `
      <div class="padel-result-detail">
        ${renderStatusSummary(record, pose, stroke)}
        ${record.status === 'completed' ? renderVideoPlayer(record) : ''}
        ${pose ? renderPoseVisibility(pose) : ''}
        ${stroke ? renderPhaseTimeline(record, stroke) : ''}
        ${stroke ? renderContactSection(record, stroke) : ''}
        ${stroke ? renderMetrics(record, stroke) : ''}
        ${config.reviewerMode ? renderCalibrationSummary() : ''}
        ${renderTechnicalFeedback(record)}
        ${renderFindings(result)}
        ${renderTechnicalDetails(record)}
      </div>
    `;
  }

  function renderFindings(result) {
    const summary = result.summary ? `<p>${escapeHtml(result.summary)}</p>` : '';
    const findings = Array.isArray(result.findings) && result.findings.length
      ? `<ul>${result.findings.map((item) => `<li>${escapeHtml(String(item))}</li>`).join('')}</ul>`
      : '';
    const recommendations = Array.isArray(result.recommendations) && result.recommendations.length
      ? `<ul>${result.recommendations.map((item) => `<li>${escapeHtml(String(item))}</li>`).join('')}</ul>`
      : '';
    if (!summary && !findings && !recommendations) return '';
    return `
      <section class="padel-result-section">
        <h4>Result summary</h4>
        ${summary}
        ${findings}
        ${recommendations}
      </section>
    `;
  }

  function renderRecord(record) {
    const existing = activeWrap?.querySelector(`[data-analysis-id="${record.id}"]`);
    const html = `
      <article class="padel-analysis-card" data-analysis-id="${record.id}" data-status="${record.status}">
        <div class="padel-analysis-head">
          <div>
            <p class="padel-card-kicker">Analysis #${record.id}</p>
            <h3>${escapeHtml(record.title || '')}</h3>
          </div>
          <span class="padel-status-badge is-${escapeHtml(record.status)}">${escapeHtml(record.status_label || '')}</span>
        </div>
        <div class="padel-analysis-meta">
          <span>Shot: ${escapeHtml(record.shot_type_label || '')}</span>
          <span>Hand: ${escapeHtml(record.dominant_hand_label || '')}</span>
          <span>Angle: ${escapeHtml(record.camera_angle_label || '')}</span>
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
        ${renderResult(record)}
        <div class="padel-actions">
          <button type="button" class="padel-button secondary padel-delete-analysis" data-analysis-id="${record.id}">מחק סרטון ורשומה</button>
        </div>
      </article>
    `;

    if (existing) {
      existing.outerHTML = html;
    } else if (activeWrap) {
      activeWrap.prepend(createFragment(html));
      if (activeWrap.querySelector('.padel-empty')) {
        activeWrap.querySelector('.padel-empty')?.remove();
      }
    }

    const card = activeWrap?.querySelector(`[data-analysis-id="${record.id}"]`);
    card?.querySelectorAll('.padel-annotated-video').forEach(loadProtectedVideo);

    if (record.status === 'uploaded' || record.status === 'processing') {
      startPolling(record.id);
    } else {
      stopPolling(record.id);
    }
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
        if (data.analysis.status === 'completed' || data.analysis.status === 'failed') {
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

  async function submitUpload(event) {
    event.preventDefault();
    if (!form) return;

    const formData = new FormData(form);
    setProgress(10, 'Uploading video...');
    setMessage('');

    try {
      const response = await fetch(`${config.restUrl}/upload`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: headers(),
        body: formData,
      });

      const data = await response.json();
      if (!response.ok || !data.analysis) {
        throw new Error(data?.message || config.strings?.uploadError || 'Upload failed');
      }

      setProgress(data.analysis.progress || 24, data.analysis.status_label || 'Uploaded');
      setMessage(config.strings?.uploadSuccess || 'Video uploaded successfully.');
      renderRecord(data.analysis);
      form.reset();
      startPolling(data.analysis.id);
    } catch (error) {
      console.error(error);
      setProgress(0, 'Ready');
      setMessage(error.message || config.strings?.uploadError || 'Upload failed', true);
    }
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
        activeWrap.innerHTML = '<p class="padel-empty">עדיין לא הועלה סרטון.</p>';
      }
    } catch (error) {
      console.error(error);
      alert(error.message || 'Delete failed');
    } finally {
      button?.removeAttribute('disabled');
    }
  }

  async function updateContactFrame(container) {
    const analysisId = container?.dataset.analysisId;
    if (!analysisId) return;
    const range = container.querySelector('.padel-contact-range');
    const selectedFrame = Number(range?.value);
    if (Number.isNaN(selectedFrame)) return;

    try {
      const response = await fetch(buildContactFrameUrl(analysisId), {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: {
          ...headers(),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ frame_index: selectedFrame }),
      });

      const data = await response.json();
      if (!response.ok || !data.analysis) {
        throw new Error(data?.message || 'Contact frame update failed');
      }

      setMessage(config.strings?.contactFrameUpdated || 'Contact frame updated successfully.');
      renderRecord(data.analysis);
    } catch (error) {
      console.error(error);
      setMessage(error.message || 'Contact frame update failed', true);
    }
  }

  function syncContactPreview(container) {
    const range = container?.querySelector('.padel-contact-range');
    if (!range) return;
    const selectedFrame = Number(range.value);
    const selectedFrameEl = container.querySelector('.padel-contact-selected-frame');
    if (selectedFrameEl) selectedFrameEl.textContent = String(selectedFrame);
  }

  function seekVideoTo(recordId, timestampMs) {
    const card = activeWrap?.querySelector(`[data-analysis-id="${recordId}"]`);
    const video = card?.querySelector('video');
    if (!video || timestampMs === null || timestampMs === undefined) return;
    const seconds = Number(timestampMs) / 1000;
    if (Number.isNaN(seconds)) return;
    video.currentTime = Math.max(0, seconds);
    video.play().catch(() => {});
  }

  form?.addEventListener('submit', submitUpload);

  activeWrap?.addEventListener('click', (event) => {
    const deleteButton = event.target.closest('.padel-delete-analysis');
    if (deleteButton) {
      deleteAnalysis(deleteButton.dataset.analysisId, deleteButton);
      return;
    }

    const phaseRow = event.target.closest('.padel-phase-row');
    if (phaseRow) {
      seekVideoTo(phaseRow.dataset.analysisId, phaseRow.dataset.phaseTime);
      return;
    }

    const stepButton = event.target.closest('.padel-contact-step');
    if (stepButton) {
      const container = stepButton.closest('.padel-contact-editor');
      const range = container?.querySelector('.padel-contact-range');
      if (!container || !range) return;
      const min = Number(range.min);
      const max = Number(range.max);
      const step = Number(stepButton.dataset.step || 0);
      const next = Math.min(max, Math.max(min, Number(range.value) + step));
      range.value = String(next);
      syncContactPreview(container);
      return;
    }

    const confirmButton = event.target.closest('.padel-contact-confirm');
    if (confirmButton) {
      updateContactFrame(confirmButton.closest('.padel-contact-editor'));
      return;
    }

    const feedbackJump = event.target.closest('.padel-feedback-jump, .padel-feedback-evidence-chip, .padel-feedback-phase-chip');
    if (feedbackJump) {
      seekVideoTo(feedbackJump.dataset.analysisId, feedbackJump.dataset.feedbackSeekTimestamp);
      return;
    }

    const reviewAddManual = event.target.closest('.padel-review-add-manual');
    if (reviewAddManual) {
      addManualMissedFindingRow(reviewAddManual.closest('.padel-review-panel'));
      return;
    }

    const reviewSave = event.target.closest('.padel-review-save');
    if (reviewSave) {
      saveReview(reviewSave.closest('.padel-review-panel'), reviewSave);
    }
  });

  activeWrap?.addEventListener('input', (event) => {
    if (event.target.matches('.padel-contact-range')) {
      syncContactPreview(event.target.closest('.padel-contact-editor'));
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
