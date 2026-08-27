const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'assets', 'js', 'match-frontend.js'),
  'utf8',
);

const instruction = source.indexOf('Select yourself once, then confirm.');
const preview = source.indexOf('class="padel-match-preview"', instruction);
const confirm = source.indexOf('class="padel-button padel-preview-confirm"', preview);
const disclosure = source.indexOf('class="padel-preview-alternates"', confirm);
const alternateFrameAction = source.indexOf('class="padel-button secondary padel-preview-step"', disclosure);

assert.notEqual(instruction, -1, 'selection instruction must be rendered');
assert.notEqual(preview, -1, 'preview image must be rendered');
assert.notEqual(confirm, -1, 'confirmation action must be rendered');
assert.notEqual(disclosure, -1, 'alternate-frame disclosure must be rendered');
assert.notEqual(alternateFrameAction, -1, 'alternate-frame controls must be inside the disclosure');
assert.ok(instruction < preview, 'instruction should appear before the preview');
assert.ok(preview < confirm, 'confirmation should appear after the preview');
assert.ok(confirm < disclosure, 'alternate frames should be secondary to confirmation');
assert.match(source, /<summary>Can't see yourself\? Choose another frame<\/summary>/);
assert.match(source, /frames\.length > 1 && !selectionLocked/);

console.log('match frontend validation ok');
