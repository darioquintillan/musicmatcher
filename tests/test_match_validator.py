"""Adversarial deterministic validation tests; no network or downloader imports."""

import copy
import json
import unittest

from musicmatcher.match_validator import choose_match, evaluate_candidate


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.track = {"title": "Canción", "artists": ["Artista"], "duration_ms": 180000,
                      "album": "Álbum", "explicit": False}
        self.candidate = {"id": "abcdefghijk", "title": "Artista - Canción (Official Audio)",
                          "track": "Canción", "artists": ["Artista"], "duration": 180,
                          "channel": "Artista - Topic", "album": "Álbum"}

    def test_exact_structured_match_is_accepted(self):
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "accepted")

    def test_does_not_change_spotify_title(self):
        original = copy.deepcopy(self.track)
        choose_match(self.track, [self.candidate])
        self.assertEqual(self.track, original)

    def test_same_title_wrong_artist_is_rejected(self):
        self.candidate["artists"] = ["Otro Intérprete"]
        self.assertTrue(evaluate_candidate(self.track, self.candidate)["rejected"])

    def test_cover_in_display_title_overrides_exact_music_metadata(self):
        self.candidate["title"] += " (Cover)"
        self.assertTrue(evaluate_candidate(self.track, self.candidate)["rejected"])

    def test_live_and_original_do_not_match(self):
        self.candidate["track"] += " (Live at Wembley)"
        self.assertTrue(evaluate_candidate(self.track, self.candidate)["rejected"])

    def test_different_specific_remix_is_rejected(self):
        self.track["title"] = "Canción - Alpha Remix"
        self.candidate["track"] = "Canción (Beta Remix)"
        self.candidate["title"] = "Artista - Canción (Beta Remix)"
        self.assertTrue(evaluate_candidate(self.track, self.candidate)["rejected"])

    def test_same_specific_remix_can_match(self):
        self.track["title"] = "Canción - Alpha Remix"
        self.candidate["track"] = "Canción (Alpha Remix)"
        self.candidate["title"] = "Artista - Canción (Alpha Remix) (Official Audio)"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "accepted")

    def test_live_forever_is_a_title_not_a_live_version(self):
        self.track["title"] = "Live Forever"
        self.candidate["track"] = "Live Forever"
        self.candidate["title"] = "Artista - Live Forever (Official Audio)"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "accepted")

    def test_take_cover_is_not_a_cover_qualifier(self):
        self.track["title"] = "Take Cover"
        self.candidate["track"] = "Take Cover"
        self.candidate["title"] = "Artista - Take Cover (Official Audio)"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "accepted")

    def test_unknown_duration_requires_review(self):
        del self.candidate["duration"]
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")

    def test_official_name_without_music_metadata_is_insufficient(self):
        del self.candidate["track"]
        del self.candidate["artists"]
        self.candidate["channel_is_verified"] = True
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")

    def test_unicode_composed_and_decomposed_match(self):
        self.candidate["track"] = "Cancio\u0301n"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "accepted")

    def test_ambiguous_near_tie_requires_review(self):
        other = {**self.candidate, "id": "12345678901"}
        self.assertEqual(choose_match(self.track, [self.candidate, other])["status"], "review")

    def test_same_video_duplicate_is_not_false_ambiguity(self):
        self.assertEqual(choose_match(self.track, [self.candidate, dict(self.candidate)])["status"], "accepted")

    def test_duration_mismatch_is_hard_rejection(self):
        self.candidate["duration"] = 200
        self.assertTrue(evaluate_candidate(self.track, self.candidate)["rejected"])

    def test_clean_candidate_for_explicit_source_is_rejected(self):
        self.track["explicit"] = True
        self.candidate["track"] += " (Clean)"
        self.assertTrue(evaluate_candidate(self.track, self.candidate)["rejected"])

    def test_explicit_source_without_explicit_evidence_requires_review(self):
        self.track["explicit"] = True
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")

    def test_explicit_source_with_explicit_evidence_can_be_accepted(self):
        self.track["explicit"] = True
        self.candidate["track"] += " (Explicit)"
        self.candidate["title"] += " (Explicit)"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "accepted")

    def test_display_title_wrong_artist_cannot_hide_behind_structured_metadata(self):
        self.candidate["title"] = "Otro Intérprete - Canción (Official Audio)"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")

    def test_missing_remaster_detail_is_not_autoaccepted(self):
        self.track["title"] = "Canción (2011 Remaster)"
        self.candidate["track"] = "Canción (Remastered)"
        self.candidate["title"] = "Canción (Remastered)"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")

    def test_invalid_video_identity_is_rejected(self):
        self.candidate["id"] = "invalid"
        self.assertTrue(evaluate_candidate(self.track, self.candidate)["rejected"])

    def test_unknown_display_version_cannot_hide_behind_structured_metadata(self):
        self.candidate["title"] = "Artista - Canción (8D Audio)"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")

    def test_short_medley_suffix_on_long_visible_title_requires_review(self):
        self.track["title"] = "This Is A Very Long Original Song Title"
        self.candidate["track"] = self.track["title"]
        self.candidate["title"] = self.track["title"] + " / X"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")

    def test_fuzzy_artist_with_extra_short_performer_requires_review(self):
        self.track["artists"] = ["The Extremely Long Artist Name"]
        self.candidate["artists"] = ["The Extremely Long Artist Name X"]
        self.candidate["title"] = "Canción"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")

    def test_medley_declared_in_description_requires_review(self):
        self.candidate["description"] = "Medley: Canción / Otra canción. Two songs in this recording."
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")

    def test_different_album_remaster_is_rejected(self):
        self.track["album"] = "Álbum (2011 Remaster)"
        self.candidate["album"] = "Álbum (2020 Remaster)"
        self.assertTrue(evaluate_candidate(self.track, self.candidate)["rejected"])

    def test_source_album_version_without_candidate_evidence_requires_review(self):
        self.track["album"] = "Álbum (2011 Remaster)"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")

    def test_matching_remaster_across_title_and_album_can_be_accepted(self):
        self.track["title"] = "Canción (2011 Remaster)"
        self.track["album"] = "Álbum (2011 Remastered)"
        self.candidate["track"] = "Canción (2011 Remastered)"
        self.candidate["title"] = "Canción (2011 Remaster)"
        self.candidate["album"] = "Álbum (2011 Remaster)"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "accepted")

    def test_different_unqualified_album_is_not_an_identity_rejection(self):
        self.candidate["album"] = "A Compilation"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "accepted")

    def test_nonfinite_durations_require_review_and_remain_serializable(self):
        for duration in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(duration=duration):
                self.candidate["duration"] = duration
                decision = choose_match(self.track, [self.candidate])
                self.assertEqual(decision["status"], "review")
                json.dumps(decision, allow_nan=False)
                self.assertIsNone(decision["candidates"][0]["candidate"]["duration"])

    def test_official_can_be_the_song_title(self):
        self.track["title"] = "Official"
        self.candidate["track"] = "Official"
        self.candidate["title"] = "Artista - Official (Official Audio)"
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "accepted")

    def test_similar_numbered_compositions_require_review(self):
        self.track["title"] = "Symphony No. 40 in G minor"
        self.candidate["track"] = "Symphony No. 41 in G minor"
        self.candidate["title"] = self.candidate["track"]
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")
        self.candidate["title"] = self.track["title"]
        self.assertEqual(choose_match(self.track, [self.candidate])["status"], "review")
