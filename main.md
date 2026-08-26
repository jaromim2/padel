# Padel Video Analysis MVP — Project Brief

## 1. Project background

We are building the first version of a personal padel video-analysis tool.

The application should allow a player to upload a short video of a single padel stroke and receive structured, understandable feedback about body movement.

This is an experimental MVP. The purpose is not to build a complete AI padel coach. The purpose is to validate that we can reliably:

1. Receive and process a short video.
2. detect the player's body landmarks.
3. calculate several repeatable movement metrics.
4. visualize the detected pose.
5. generate useful feedback based on transparent rules.
6. save the result for future comparison.

The existing product may already include a WordPress backend and a PWA frontend. Before implementing anything, inspect the repository and document the existing architecture.

Do not rewrite unrelated parts of the application.

---

# 2. MVP product objective

The first version supports one controlled use case:

## Supported stroke

Forehand groundstroke only.

The stroke should be filmed from the side.

The player should be fully visible, including:

* head;
* shoulders;
* arms;
* hips;
* knees;
* feet.

The video should show:

* starting position;
* preparation;
* forward movement;
* approximate contact moment;
* follow-through;
* recovery.

## Supported video constraints

Initially accept:

* MP4;
* MOV;
* WebM.

Initial limits:

* maximum duration: 35 seconds;
* minimum duration: 2 seconds;
* maximum upload size: configurable, initially 75 MB;
* one visible player;
* landscape or portrait video;
* recommended minimum resolution: 720p;
* recommended frame rate: 30 FPS or higher.

The limits must be stored in configuration rather than duplicated as hardcoded values.

---

# 3. User flow

The user should be able to perform the following flow:

1. Open the “Padel Analysis” page.
2. Select “Forehand”.
3. Select dominant hand:

   * right;
   * left.
4. Select camera angle:

   * side;
   * rear;
   * unknown.

For the first MVP, only `side` should be considered fully supported.

5. Upload or record a short video.
6. See clear filming instructions before submission.
7. Submit the video.
8. See an analysis status:

   * uploaded;
   * validating;
   * processing;
   * completed;
   * failed.
9. Open the result.
10. See:

* original video;
* annotated video;
* analysis confidence;
* selected contact frame;
* calculated metrics;
* up to three feedback items;
* one recommended drill.

11. Optionally correct the selected contact frame and rerun the metric calculation.
12. Delete the analysis and its stored video.

---

# 4. Definition of success

The MVP is successful when a correctly filmed forehand video produces a repeatable result containing:

* usable body landmarks for most relevant frames;
* an annotated video or annotated key frames;
* a structured metrics response;
* no more than three feedback observations;
* one practical drill;
* a confidence level;
* a clear warning when confidence is low.

The system must not invent observations when the body cannot be detected reliably.

The same input video should produce the same metrics, apart from a small documented numeric tolerance.

The first version does not need to prove that the feedback is biomechanically perfect. It needs to prove that the technical pipeline is stable and that the feedback format is useful enough for manual evaluation.

---

# 5. Architecture

Prefer a modular architecture.

Suggested high-level components:

```text
Existing PWA or web frontend
        |
        | REST API
        v
FastAPI analysis service
        |
        +-- video validation
        +-- video normalization
        +-- pose extraction
        +-- metric calculation
        +-- rule-based feedback
        +-- annotated output generation
        |
        +-- database
        +-- object storage
```

## Development environment

The project should run locally through Docker Compose.

Suggested services:

```text
frontend, if a separate frontend is required
api
worker or processing service
postgres
minio
```

For the earliest local proof of concept, a separate queue service is optional.

However, video processing must be isolated behind a job/service abstraction so that an external worker queue can be added later without rewriting the API.

Do not make the HTTP upload request wait until the entire video has been analyzed.

## Storage abstraction

Support two storage implementations:

1. local filesystem for tests and simple development;
2. S3-compatible storage for MinIO or S3.

Application code must interact through a storage interface rather than directly accessing MinIO everywhere.

## Database

Use PostgreSQL when integrating into the full environment.

SQLite may be used only for an isolated local prototype, provided the persistence layer is structured so PostgreSQL can replace it cleanly.

---

# 6. Suggested repository structure

Adapt this structure to the existing repository rather than forcing it unnecessarily:

```text
padel-analysis/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   │   ├── analyses.py
│   │   │   │   └── health.py
│   │   │   └── dependencies.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── logging.py
│   │   │   └── errors.py
│   │   ├── db/
│   │   │   ├── models.py
│   │   │   ├── schemas.py
│   │   │   └── repository.py
│   │   ├── services/
│   │   │   ├── analysis_service.py
│   │   │   ├── video_service.py
│   │   │   ├── pose_service.py
│   │   │   ├── metrics_service.py
│   │   │   ├── feedback_service.py
│   │   │   ├── overlay_service.py
│   │   │   └── storage_service.py
│   │   ├── domain/
│   │   │   ├── landmarks.py
│   │   │   ├── metrics.py
│   │   │   ├── feedback.py
│   │   │   └── analysis_status.py
│   │   ├── config/
│   │   │   └── strokes/
│   │   │       └── forehand.yaml
│   │   └── main.py
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── fixtures/
├── frontend/
├── docs/
│   ├── architecture.md
│   ├── filming-guide.md
│   ├── metrics.md
│   └── limitations.md
├── scripts/
├── docker-compose.yml
├── .env.example
├── AGENTS.md
└── README.md
```

---

# 7. Core data model

Create an `Analysis` entity with at least:

```text
id
user_id or external_user_id
stroke_type
dominant_hand
camera_angle
status
status_message
source_video_path
normalized_video_path
annotated_video_path
duration_ms
width
height
fps
total_frames
contact_frame
contact_frame_source
pose_coverage
confidence_score
confidence_level
metrics_json
feedback_json
processing_time_ms
error_code
error_message
created_at
updated_at
completed_at
```

## Contact frame source

Possible values:

```text
automatic
manual
unknown
```

## Status values

```text
uploaded
validating
queued
processing
completed
failed
deleted
```

Use a real enum or equivalent type-safe implementation.

---

# 8. API design

Implement versioned endpoints.

## Create analysis

```http
POST /api/v1/analyses
```

Use multipart form data.

Fields:

```text
video
stroke_type
dominant_hand
camera_angle
```

Initial accepted stroke:

```text
forehand
```

The endpoint should:

1. validate basic file metadata;
2. save the source securely;
3. create an analysis record;
4. trigger processing;
5. return HTTP 202 with the analysis ID and status.

Example response:

```json
{
  "id": "analysis-id",
  "status": "uploaded",
  "status_url": "/api/v1/analyses/analysis-id"
}
```

## Get status and result

```http
GET /api/v1/analyses/{analysis_id}
```

Return the current status.

When complete, include the complete result or a result URL.

## Set manual contact frame

```http
PATCH /api/v1/analyses/{analysis_id}/contact-frame
```

Example body:

```json
{
  "frame": 84
}
```

This should rerun metric and feedback generation without rerunning pose detection unless necessary.

## Delete analysis

```http
DELETE /api/v1/analyses/{analysis_id}
```

Delete or mark the database record as deleted and remove stored video objects according to the retention policy.

## Health endpoints

```http
GET /health/live
GET /health/ready
```

Readiness should verify required dependencies.

---

# 9. Video-processing pipeline

Implement the pipeline as explicit stages.

Each stage should produce structured logs and update the analysis status.

## Stage 1: validation

Validate:

* MIME type;
* actual container type;
* file size;
* duration;
* resolution;
* decodable video stream;
* at least one usable video frame.

Do not trust the uploaded filename or client MIME type.

Return safe user-facing errors.

Examples:

```text
VIDEO_TOO_LONG
VIDEO_TOO_LARGE
UNSUPPORTED_FORMAT
VIDEO_NOT_DECODABLE
NO_VIDEO_STREAM
```

## Stage 2: normalization

Use FFmpeg or an equivalent reliable video-processing tool.

Normalize the video to a predictable internal format:

```text
MP4
H.264
no audio required
720p maximum while preserving aspect ratio
constant frame rate
30 FPS for the initial implementation
rotation metadata applied
```

Keep the original video separate from the normalized video.

## Stage 3: frame extraction

Read all normalized frames.

For each frame store or process:

```text
frame index
timestamp
image dimensions
pose detection result
landmark confidence values
```

Avoid permanently storing every extracted image unless required for debugging.

## Stage 4: pose extraction

Use a pose-estimation component through a `PoseEstimator` interface.

Initial implementation:

```text
MediaPipe Pose Landmarker
video mode
one pose
```

The rest of the application must not depend directly on MediaPipe-specific object types.

Convert results into internal domain objects.

Example internal landmark:

```json
{
  "name": "LEFT_SHOULDER",
  "x": 0.42,
  "y": 0.31,
  "z": -0.08,
  "visibility": 0.96,
  "presence": 0.98
}
```

Store normalized coordinates and frame timestamps.

## Stage 5: landmark quality checks

Calculate pose coverage:

```text
frames with required landmarks / relevant total frames
```

Required landmarks for the first forehand analysis include:

```text
left shoulder
right shoulder
left elbow
right elbow
left wrist
right wrist
left hip
right hip
left knee
right knee
left ankle
right ankle
```

Do not calculate a metric when its required landmarks are below the configured confidence threshold.

## Stage 6: smoothing

Raw landmarks may jump between frames.

Create a separate smoothing component.

Start with a simple configurable method, such as:

* moving average;
* exponential moving average;
* another well-tested deterministic filter.

Do not hide the raw values.

The code should make it possible to compare raw and smoothed metrics during debugging.

## Stage 7: contact-frame estimation

Automatic contact-frame estimation is experimental.

For the first version, estimate the contact frame using movement signals such as:

* dominant wrist velocity;
* dominant elbow velocity;
* arm extension;
* movement direction change.

Do not claim this is the true ball-contact frame.

Return:

```text
estimated frame
confidence
candidate frame range
```

The UI must allow the user to adjust the frame manually.

## Stage 8: metric calculation

Calculate only metrics that can be explained and tested.

Initial metric candidates:

### A. Knee flexion at contact

Calculate left and right knee angles.

Store:

```text
lead_knee_angle
trail_knee_angle
minimum_knee_angle
```

### B. Elbow angle

Calculate the dominant elbow angle at:

```text
preparation
estimated contact
follow-through
```

### C. Shoulder-line rotation

Estimate shoulder-line orientation using left and right shoulder points.

Store the value relative to the image plane.

Do not describe this as a true three-dimensional body rotation.

### D. Hip-line rotation

Estimate hip-line orientation using left and right hip points.

### E. Wrist-speed profile

Calculate normalized dominant-wrist displacement over time.

Store:

```text
peak velocity
peak velocity frame
velocity around contact
```

Clearly document that this is image-space movement and not real-world speed in km/h.

### F. Balance proxy

Create a simple balance proxy based on:

* body center relative to the foot support area;
* sudden body-center movement;
* whether relevant feet are visible.

Call this a proxy, not a medical or biomechanical diagnosis.

### G. Recovery proxy

Estimate the number of milliseconds between the follow-through peak and return toward a configured ready-position range.

The first implementation may mark this metric unavailable when detection confidence is insufficient.

---

# 10. Metric geometry

Place all geometric functions in pure, independently testable code.

Examples:

```text
calculate_angle(point_a, point_b, point_c)
calculate_distance(point_a, point_b)
calculate_midpoint(point_a, point_b)
calculate_velocity(position_series, timestamps)
calculate_line_orientation(point_a, point_b)
```

Requirements:

* type hints;
* docstrings;
* no access to database or video files;
* handle missing landmarks safely;
* avoid division by zero;
* unit tests with known geometric examples.

---

# 11. Feedback engine

Do not use a generative AI model in the initial feedback engine.

Start with deterministic rules.

Rules must be stored outside the main source code where practical.

Suggested file:

```text
backend/app/config/strokes/forehand.yaml
```

Example conceptual structure:

```yaml
stroke: forehand
version: 1

rules:
  - id: low_knee_bend
    metric: minimum_knee_angle
    condition: configured_range
    priority: medium
    message_he: "כיפוף הברכיים היה מוגבל ברגע המגע המשוער."
    drill_id: shadow_forehand_knee_bend

  - id: unstable_finish
    metric: balance_proxy
    condition: configured_range
    priority: high
    message_he: "בסיום החבטה נראית תנועה לא יציבה של מרכז הגוף."
    drill_id: controlled_forehand_finish
```

Do not invent professional threshold values.

Use placeholder or provisional thresholds clearly marked as experimental.

Every threshold must include:

```text
source
version
notes
status: experimental or validated
```

## Feedback response

Return a maximum of three feedback items.

Each feedback item should contain:

```json
{
  "rule_id": "low_knee_bend",
  "title": "כיפוף ברכיים",
  "observation": "כיפוף הברכיים היה מוגבל ברגע המגע המשוער.",
  "why_it_matters": "עמדה יציבה ונמוכה יותר עשויה לעזור לשליטה בתנועה.",
  "suggestion": "נסה להתחיל את התנועה בעמדה מעט נמוכה יותר.",
  "confidence": 0.78,
  "evidence": {
    "frame": 84,
    "metric": "minimum_knee_angle",
    "value": 161.4
  }
}
```

The response must distinguish between:

* measured observation;
* interpretation;
* suggested practice.

Never return feedback for a metric that is unavailable or low-confidence.

## Recommended drill

Return one drill associated with the highest-priority reliable observation.

Example:

```json
{
  "id": "controlled_forehand_finish",
  "name": "פור-הנד צל עם עצירה",
  "instructions": [
    "בצע תנועה ללא כדור.",
    "עצור לשתי שניות בסיום.",
    "בדוק שהגוף יציב ושתי הרגליים נשארות בשליטה."
  ],
  "sets": 3,
  "repetitions": 8,
  "safety_note": "הפסק במקרה של כאב."
}
```

Do not provide medical diagnosis or injury-treatment advice.

---

# 12. Confidence system

Confidence is a core product feature, not an optional detail.

Calculate confidence from factors such as:

```text
pose coverage
required landmark visibility
camera-angle support
contact-frame confidence
motion continuity
player fully inside frame
```

Return:

```text
high
medium
low
insufficient
```

When confidence is insufficient, return filming guidance instead of technical criticism.

Example:

```json
{
  "confidence_level": "insufficient",
  "feedback": [],
  "retake_guidance": [
    "מקם את המצלמה רחוק יותר.",
    "ודא שכפות הרגליים נשארות בתוך התמונה.",
    "צלם מהצד ובתאורה טובה."
  ]
}
```

---

# 13. Annotated output

Generate either:

1. an annotated MP4; or
2. annotated key-frame images for the first milestone.

The annotation should show:

* pose skeleton;
* estimated contact frame;
* relevant measured angles;
* frame number or timestamp;
* low-confidence warning when needed.

Avoid adding too much text over the video.

Create a debug mode that can show more details without exposing them in the normal user interface.

---

# 14. Frontend requirements

Use the existing frontend technology when practical.

Do not introduce a second frontend framework without a strong reason.

Required UI components:

## Upload screen

Include:

* stroke selector;
* dominant-hand selector;
* camera-angle selector;
* video picker;
* filming instructions;
* duration and size limitations;
* upload progress;
* validation errors.

## Processing screen

Include:

* current status;
* non-fake progress indicator;
* ability to leave the page and return;
* retry option after a recoverable failure.

## Result screen

Include:

* confidence badge;
* original or annotated video;
* estimated contact-frame selector;
* metrics summary;
* up to three feedback cards;
* recommended drill;
* limitations notice;
* delete button.

All user-facing text should initially support Hebrew and RTL.

Technical identifiers, API payload keys and code should remain in English.

---

# 15. Privacy and security

Implement basic protections from the start:

* authenticated access when integrated with the existing product;
* analysis ownership checks;
* non-public video storage;
* signed or protected media access;
* safe generated filenames;
* file-type validation;
* upload-size limits;
* no execution of uploaded content;
* configurable retention period;
* deletion endpoint;
* avoid logging sensitive video URLs;
* no secrets committed to the repository.

Create `.env.example`, but never include real credentials.

Add a clear note that videos may contain other people and should only be uploaded when the user is allowed to upload them.

---

# 16. Error handling

Create stable error codes.

Examples:

```text
ANALYSIS_NOT_FOUND
ACCESS_DENIED
VIDEO_TOO_LONG
VIDEO_TOO_LARGE
UNSUPPORTED_VIDEO_FORMAT
VIDEO_DECODE_FAILED
POSE_NOT_DETECTED
INSUFFICIENT_POSE_COVERAGE
PROCESSING_FAILED
STORAGE_ERROR
```

Store detailed internal errors in logs.

Return safe and understandable messages to the client.

---

# 17. Logging and observability

Use structured logs containing:

```text
analysis_id
processing_stage
duration_ms
frame_count
pose_coverage
confidence_level
error_code
```

Never log the full video contents or secrets.

Measure processing time for each pipeline stage.

Add a simple command or report that prints:

```text
validation time
normalization time
pose extraction time
metrics time
overlay time
total time
```

---

# 18. Testing requirements

## Unit tests

Cover:

* angle calculations;
* distance calculations;
* velocity calculations;
* line orientation;
* missing landmarks;
* confidence calculation;
* feedback-rule evaluation;
* status transitions.

## Integration tests

Cover:

* successful upload;
* invalid file;
* unsupported format;
* too-long video;
* processing failure;
* completed analysis response;
* manual contact-frame update;
* deletion.

## Video fixtures

Keep very small licensed or self-created test fixtures.

Do not commit large private videos.

Where possible, generate synthetic landmark sequences for metric tests instead of depending on real videos.

## Determinism test

Analyze the same fixture multiple times and verify that calculated metrics remain within a documented tolerance.

---

# 19. Documentation deliverables

Create:

## README.md

Include:

* project purpose;
* supported MVP flow;
* requirements;
* local startup commands;
* environment variables;
* test commands;
* API examples;
* known limitations.

## docs/architecture.md

Include:

* system components;
* processing sequence;
* storage flow;
* job lifecycle;
* integration points with the existing PWA or WordPress system.

## docs/metrics.md

For every metric document:

```text
definition
required landmarks
formula
units
limitations
confidence requirements
experimental thresholds
```

## docs/filming-guide.md

Hebrew filming instructions:

* film from the side;
* keep the whole body visible;
* use stable camera placement;
* use sufficient lighting;
* record one stroke;
* avoid other people crossing in front;
* avoid digital zoom when possible.

## docs/limitations.md

Clearly state:

* no ball detection;
* no racket detection;
* no tactical analysis;
* contact frame is estimated;
* image-space measurements are not real-world measurements;
* feedback rules are experimental;
* results are not medical advice.

---

# 20. AGENTS.md instructions

Create an `AGENTS.md` file containing project-specific guidance for future coding tasks.

Include:

* run existing tests before and after changes;
* do not change public API contracts without documenting the change;
* keep pose-estimation vendor objects inside the pose adapter;
* keep geometry functions pure;
* do not hardcode biomechanical thresholds in route handlers;
* do not claim unsupported accuracy;
* preserve Hebrew RTL support;
* never commit secrets or private videos;
* update documentation when metrics or API contracts change.

---

# 21. Implementation phases

Do not implement the whole system in one unreviewed change.

## Phase 0 — repository audit

Deliver only:

* current architecture summary;
* relevant existing files;
* integration options;
* risks;
* proposed file changes;
* unanswered technical questions;
* revised implementation plan.

Do not modify production code in this phase.

## Phase 1 — runnable skeleton

Deliver:

* FastAPI service;
* configuration;
* database entity;
* upload endpoint;
* status endpoint;
* local storage;
* basic validation;
* Docker setup;
* API tests.

Do not add pose estimation yet.

## Phase 2 — offline pose proof of concept

Deliver a CLI command:

```bash
python -m app.cli.analyze_video path/to/video.mp4
```

The command should:

* normalize the video;
* run pose detection;
* write landmarks to JSON;
* calculate pose coverage;
* generate annotated key frames;
* print a summary.

This phase should work before connecting pose processing to the web API.

## Phase 3 — metric engine

Deliver:

* pure geometry functions;
* smoothed landmark series;
* contact-frame estimation;
* initial metrics;
* confidence calculation;
* unit tests;
* metrics documentation.

## Phase 4 — feedback engine

Deliver:

* external rule configuration;
* Hebrew feedback templates;
* maximum-three-feedback selection;
* drill selection;
* low-confidence behavior;
* rule-engine tests.

## Phase 5 — asynchronous API integration

Connect the offline analyzer to the analysis job lifecycle.

Deliver:

* processing status changes;
* background execution abstraction;
* output storage;
* result API;
* retry-safe behavior;
* integration tests.

## Phase 6 — frontend

Deliver the complete user flow:

* upload;
* processing;
* result;
* contact-frame correction;
* delete.

## Phase 7 — validation

Create a manual evaluation procedure using several self-recorded videos.

For every video record:

```text
filming quality
pose coverage
contact-frame quality
metric stability
feedback usefulness
processing time
failures
```

Do not tune thresholds based on one video only.

---

# 22. Coding standards

Use:

* Python type hints;
* clear domain models;
* small focused functions;
* dependency injection where useful;
* configuration through environment variables;
* structured errors;
* explicit interfaces around storage and pose estimation;
* automated formatting and linting;
* testable code without hidden global state.

Avoid:

* premature microservices;
* unnecessary abstractions;
* unreviewed dependency upgrades;
* giant route handlers;
* AI-generated feedback without evidence;
* hardcoded paths;
* hardcoded secrets;
* large binary files in Git;
* unrelated refactoring.

---

# 23. Required response format from Codex

Before changing code, respond with:

1. repository findings;
2. assumptions;
3. proposed architecture;
4. exact files to add or modify;
5. risks and limitations;
6. implementation sequence;
7. commands that will be used for validation.

After each implementation phase, respond with:

1. summary of changes;
2. files changed;
3. tests run;
4. test results;
5. manual validation steps;
6. known limitations;
7. recommended next phase.

Do not report a test as passing unless it was actually executed.

Do not silently replace existing application architecture.

When information is missing, choose the smallest reversible implementation and document the assumption.
