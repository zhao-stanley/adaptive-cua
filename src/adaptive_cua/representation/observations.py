"""Stateful full/delta observations, selective images, and visual references."""

import base64
import json
from dataclasses import dataclass

from adaptive_cua.browser.models import BrowserSnapshot
from adaptive_cua.policy.adaptive import AdaptivePolicy
from adaptive_cua.representation.structure import diff, meaningful_change, page_key, visible_structure
from adaptive_cua.representation.visual import changed_pixels, make_visual, screenshot_image, visual_candidates


@dataclass
class PreparedObservation:
    data: dict
    images: list[bytes]

    def content(self) -> list[dict]:
        return [{"type": "input_text", "text": json.dumps(self.data, ensure_ascii=False)}] + [
            {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png).decode()}
            for png in self.images
        ]


class AdaptiveObservations:
    def __init__(self, *, mode="adaptive", full_refresh_interval=12, max_crops=4, max_crop_fraction=0.5):
        self.mode = mode
        self.full_refresh_interval = full_refresh_interval
        self.max_crops = max_crops
        self.max_crop_fraction = max_crop_fraction
        self.reset()

    def reset(self):
        """Call whenever the model loses its prior observations (e.g. a context reset)."""
        self.previous = None
        self.structure = {}
        self.visual_sources = {}
        self.since_full = 0
        self.policy = AdaptivePolicy()

    def prepare(
        self, snapshot: BrowserSnapshot, *, action=None, arguments=None, status="ok", requested="auto", force_full=False
    ) -> PreparedObservation:
        if requested not in {"auto", "full", "crops", "screenshot"}:
            raise ValueError("Unknown observation detail.")
        current = visible_structure(snapshot)
        reset = (
            force_full
            or self.previous is None
            or page_key(snapshot) != page_key(self.previous)
            or snapshot.version <= self.previous.version
        )
        image = screenshot_image(snapshot)
        changes = diff(self.structure, current)
        metadata_changed = self.previous is not None and (
            snapshot.title != self.previous.title or snapshot.tabs != self.previous.tabs
        )
        checkpoint = bool(self.full_refresh_interval and self.since_full >= self.full_refresh_interval)
        decision = self.policy.decide(
            reset=reset,
            changed=meaningful_change(self.structure, current) or metadata_changed,
            visual_changed=changed_pixels(None if reset else self.previous.screenshot, image),
            action=action,
            arguments=arguments or {},
            status=status,
            requested=requested,
            checkpoint=checkpoint,
            previous_structure=self.structure,
        )
        reasons = list(decision.reasons)
        regions = visual_candidates(snapshot, image) if self.mode == "adaptive" else []
        area = sum((right - left) * (bottom - top) for left, top, right, bottom in regions)
        full_image = decision.screenshot or self.mode == "full"
        if regions and (len(regions) > self.max_crops or area > image.width * image.height * self.max_crop_fraction):
            full_image = True
            reasons.append("crop_budget_exceeded")
        if requested == "crops" and not regions:
            full_image = True
            reasons.append("no_crop_candidates_full_context_requested")
        if regions:
            reasons.append("unrepresented_visual_content")
        visuals = (
            [make_visual(image, (0, 0, image.width, image.height), "screenshot")]
            if full_image
            else [make_visual(image, region) for region in regions]
        )
        sources = {} if reset else self.visual_sources
        force_images = reset or requested in {"screenshot", "crops"} or decision.screenshot or self.mode == "full"
        images, descriptors, next_sources = [], [], {}
        for visual in visuals:
            send = force_images or visual.id not in sources
            source_version = snapshot.version if send else sources[visual.id]
            descriptor = {
                "id": visual.id,
                "kind": visual.kind,
                "bounds": visual.bounds,
                "coordinate_space": "viewport_css",
                "scale": 1,
                "source_observation_version": source_version,
                "image_index": len(images) if send else None,
            }
            descriptors.append(descriptor)
            next_sources[visual.id] = source_version
            if send:
                images.append(visual.png)
        full_structure = decision.full_structure or self.mode == "full"
        data = {
            "observation_version": snapshot.version,
            "document_id": snapshot.document_id,
            "tab_id": snapshot.tab_id,
            "url": snapshot.url,
            "title": snapshot.title,
            "viewport": snapshot.viewport,
            "tabs": snapshot.tabs,
            "warnings": snapshot.warnings,
            "structure_mode": "full" if full_structure else "delta",
            "base_version": None if full_structure else self.previous.version,
            "visuals": descriptors,
            "removed_visuals": sorted(set(self.visual_sources) - set(next_sources)),
            "level": ("screenshot" if full_image else "crops")
            if images
            else ("structure" if full_structure else "changes"),
            "reasons": reasons or ["routine_update"],
        }
        if full_structure:
            data["elements"] = list(current.values())
        else:
            data.update(changes)
        if reset:
            data["reset_visuals"] = True
        # Commit the new baseline only after construction succeeds. Captures not sent
        # to this object do not become delta dependencies.
        self.previous = snapshot
        self.structure = current
        self.visual_sources = next_sources
        self.since_full = 0 if full_structure else self.since_full + 1
        return PreparedObservation(data, images)
