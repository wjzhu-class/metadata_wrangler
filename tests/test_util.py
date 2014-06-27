from collections import defaultdict
from nose.tools import eq_, set_trace

from util import (
    LanguageCodes,
    MetadataSimilarity,
)

class TestLanguageCodes(object):

    def test_lookups(self):
        c = LanguageCodes
        eq_("eng", c.two_to_three['en'])
        eq_("en", c.three_to_two['eng'])
        eq_(["English"], c.english_names['en'])
        eq_(["English"], c.english_names['eng'])

        eq_("spa", c.two_to_three['es'])
        eq_("es", c.three_to_two['spa'])
        eq_(["Spanish", "Castilian"], c.english_names['es'])
        eq_(["Spanish", "Castilian"], c.english_names['spa'])

        eq_("chi", c.two_to_three['zh'])
        eq_("zh", c.three_to_two['chi'])
        eq_(["Chinese"], c.english_names['zh'])
        eq_(["Chinese"], c.english_names['chi'])

        eq_(None, c.two_to_three['nosuchlanguage'])
        eq_(None, c.three_to_two['nosuchlanguage'])
        eq_([], c.english_names['nosuchlanguage'])

    def test_locale(self):
        m = LanguageCodes.iso_639_2_for_locale
        eq_("eng", m("en-US"))
        eq_("eng", m("en"))
        eq_("eng", m("en-GB"))
        eq_(None, m("nq-none"))

class DummyAuthor(object):

    def __init__(self, name, aliases=[]):
        self.name = name
        self.aliases = aliases


class TestMetadataSimilarity(object):

    def test_identity(self):
        """Verify that we ignore the order of words in titles,
        as well as non-alphanumeric characters."""

        eq_(1, MetadataSimilarity.title_similarity("foo bar", "foo bar"))
        eq_(1, MetadataSimilarity.title_similarity("foo bar", "bar, foo"))
        eq_(1, MetadataSimilarity.title_similarity("foo bar.", "FOO BAR"))

    def test_histogram_distance(self):

        # These two sets of titles generate exactly the same histogram.
        # Their distance is 0.
        a1 = ["The First Title", "The Second Title"]
        a2 = ["title the second", "FIRST, THE TITLE"]
        eq_(0, MetadataSimilarity.histogram_distance(a1, a2))

        # These two sets of titles are as far apart as it's
        # possible to be. Their distance is 1.
        a1 = ["These Words Have Absolutely"]
        a2 = ["Nothing In Common, Really"]
        eq_(1, MetadataSimilarity.histogram_distance(a1, a2))

        # Now we test a difficult real-world case.

        # "Tom Sawyer Abroad" and "Tom Sawyer, Detective" are
        # completely different books by the same author. Their titles
        # differ only by one word. They are frequently anthologized
        # together, so OCLC maps them to plenty of the same
        # titles. They are also frequently included with other stories,
        # which adds random junk to the titles.
        abroad = ["Tom Sawyer abroad",
                  "The adventures of Tom Sawyer, Tom Sawyer abroad [and] Tom Sawyer, detective",
                  "Tom Sawyer abroad",
                  "Tom Sawyer abroad",
                  "Tom Sawyer Abroad",
                  "Tom Sawyer Abroad",
                  "Tom Sawyer Abroad",
                  "Tom Sawyer abroad : and other stories",
                  "Tom Sawyer abroad Tom Sawyer, detective : and other stories, etc. etc.",
                  "Tom Sawyer abroad",
                  "Tom Sawyer abroad",
                  "Tom Sawyer abroad",
                  "Tom Sawyer abroad and other stories",
                  "Tom Sawyer abroad and other stories",
                  "Tom Sawyer abroad and the American claimant,",
                  "Tom Sawyer abroad and the American claimant",
                  "Tom Sawyer abroad : and The American claimant: novels.",
                  "Tom Sawyer abroad : and The American claimant: novels.",
                  "Tom Sawyer Abroad - Tom Sawyer, Detective",
              ]

        detective = ["Tom Sawyer, Detective",
                     "Tom Sawyer Abroad - Tom Sawyer, Detective",
                     "Tom Sawyer Detective : As Told by Huck Finn : And Other Tales.",
                     "Tom Sawyer, Detective",
                     "Tom Sawyer, Detective.",
                     "The adventures of Tom Sawyer, Tom Sawyer abroad [and] Tom Sawyer, detective",
                     "Tom Sawyer detective : and other stories every child should know",
                     "Tom Sawyer, detective : as told by Huck Finn and other tales",
                     "Tom Sawyer, detective, as told by Huck Finn and other tales...",
                     "The adventures of Tom Sawyer, Tom Sawyer abroad [and] Tom Sawyer, detective,",
                     "Tom Sawyer abroad, Tom Sawyer, detective, and other stories",
                     "Tom Sawyer, detective",
                     "Tom Sawyer, detective",
                     "Tom Sawyer, detective",
                     "Tom Sawyer, detective",
                     "Tom Sawyer, detective",
                     "Tom Sawyer, detective",
                     "Tom Sawyer abroad Tom Sawyer detective",
                     "Tom Sawyer, detective : as told by Huck Finn",
                     "Tom Sawyer : detective",
                 ]


        # The histogram distance of the two sets of titles is not
        # huge, but it is significant.
        d = MetadataSimilarity.histogram_distance(abroad, detective)

        # The histogram distance between two lists is symmetrical, within
        # a small range of error for floating-point rounding.
        difference = d - MetadataSimilarity.histogram_distance(
            detective, abroad)
        assert abs(difference) < 0.000001

        # The histogram distance between the Gutenberg title of a book
        # and the set of all OCLC Classify titles for that book tends
        # to be fairly small.
        ab_ab = MetadataSimilarity.histogram_distance(
            ["Tom Sawyer Abroad"], abroad)
        de_de = MetadataSimilarity.histogram_distance(
            ["Tom Sawyer, Detective"], detective)

        assert ab_ab < 0.5
        assert de_de < 0.5

        # The histogram distance between the Gutenberg title of a book
        # and the set of all OCLC Classify titles for that book tends
        # to be larger.
        ab_de = MetadataSimilarity.histogram_distance(
            ["Tom Sawyer Abroad"], detective)
        de_ab = MetadataSimilarity.histogram_distance(
            ["Tom Sawyer, Detective"], abroad)

        assert ab_de > 0.5
        assert de_ab > 0.5

        # n.b. in real usage the likes of "Tom Sawyer Abroad" will be
        # much more common than the likes of "Tom Sawyer Abroad - Tom
        # Sawyer, Detective", so the difference in histogram
        # difference will be even more stark.

    def _arrange_by_confidence_level(self, title, *other_titles):
        matches = defaultdict(list)
        stopwords = set(["the", "a", "an"])
        for other_title in other_titles:
            distance = MetadataSimilarity.histogram_distance(
                [title], [other_title], stopwords)
            similarity = 1-distance
            for confidence_level in 1, 0.8, 0.5, 0.25, 0:
                if similarity >= confidence_level:
                    matches[confidence_level].append(other_title)
                    break
        return matches

    def test_title_similarity(self):
        """Demonstrate how the title similarity algorithm works in common
        cases."""

        # These are some titles OCLC gave us when we asked for Moby
        # Dick.  Some of them are Moby Dick, some are compilations
        # that include Moby Dick, some are books about Moby Dick, some
        # are abridged versions of Moby Dick.
        moby = self._arrange_by_confidence_level(
            "Moby Dick",

            "Moby Dick",
            "Moby-Dick",
            "Moby Dick Selections",
            "Moby Dick; notes",
            "Moby Dick; or, The whale",
            "Moby Dick, or, The whale",
            "The best of Herman Melville : Moby Dick : Omoo : Typee : Israel Potter.",
            "The best of Herman Melville",
            "Redburn : his first voyage",
            "Redburn, his first voyage : being the sailorboy confessions and reminiscences of the son-of-a-gentleman in the merchant service",
            "Redburn, his first voyage ; White-jacket, or, The world in a man-of-war ; Moby-Dick, or, The whale",
            "Ishmael's white world : a phenomenological reading of Moby Dick.",
            "Moby-Dick : an authoritative text, reviews and letters",
        )

        # These are all the titles that are even remotely similar to
        # "Moby Dick" according to the histogram distance algorithm.
        eq_(["Moby Dick", "Moby-Dick"], sorted(moby[1]))
        eq_([], sorted(moby[0.8]))
        eq_(['Moby Dick Selections',
             'Moby Dick, or, The whale',
             'Moby Dick; notes',
             'Moby Dick; or, The whale',
             ],
            sorted(moby[0.5]))
        eq_(['Moby-Dick : an authoritative text, reviews and letters'],
            sorted(moby[0.25]))

        # Similarly for an edition of Huckleberry Finn with an
        # unusually long name.
        huck = self._arrange_by_confidence_level(
            "The Adventures of Huckleberry Finn (Tom Sawyer's Comrade)",

            "Adventures of Huckleberry Finn",
            "The Adventures of Huckleberry Finn",
            'Adventures of Huckleberry Finn : "Tom Sawyer\'s comrade", scene: the Mississippi Valley, time: early nineteenth century',
            "The adventures of Huckleberry Finn : (Tom Sawyer's Comrade) : Scene: The Mississippi Valley, Time: Firty to Fifty Years Ago : In 2 Volumes : Vol. 1-2.",
            "The adventures of Tom Sawyer",
            )

        # Note that from a word frequency perspective, "The adventures
        # of Tom Sawyer" is just as likely as "The adventures of
        # Huckleberry Finn". This is the sort of mistake that has to
        # be cleaned up later.
        eq_([], huck[1])
        eq_([], huck[0.8])
        eq_([
            'Adventures of Huckleberry Finn',
            'Adventures of Huckleberry Finn : "Tom Sawyer\'s comrade", scene: the Mississippi Valley, time: early nineteenth century',
            'The Adventures of Huckleberry Finn',
            'The adventures of Tom Sawyer'
        ],
            sorted(huck[0.5]))
        eq_([
            "The adventures of Huckleberry Finn : (Tom Sawyer's Comrade) : Scene: The Mississippi Valley, Time: Firty to Fifty Years Ago : In 2 Volumes : Vol. 1-2."
        ],
            huck[0.25])

        # An edition of Huckleberry Finn with a different title.
        huck2 = self._arrange_by_confidence_level(
            "Adventures of Huckleberry Finn",
           
            "The adventures of Huckleberry Finn",
            "Huckleberry Finn",
            "Mississippi writings",
            "The adventures of Tom Sawyer",
            "The adventures of Tom Sawyer and the adventures of Huckleberry Finn",
            "Adventures of Huckleberry Finn : a case study in critical controversy",
            "Adventures of Huckleberry Finn : an authoritative text, contexts and sources, criticism",
            "Tom Sawyer and Huckleberry Finn",
            "Mark Twain : four complete novels.",
            "The annotated Huckleberry Finn : Adventures of Huckleberry Finn (Tom Sawyer's comrade)",
            "The annotated Huckleberry Finn : Adventures of Huckleberry Finn",
            "Tom Sawyer. Huckleberry Finn.",
        )

        eq_(['The adventures of Huckleberry Finn'], huck2[1])

        eq_([], huck2[0.8])

        eq_([
            'Huckleberry Finn',
            'The adventures of Tom Sawyer',
            'The adventures of Tom Sawyer and the adventures of Huckleberry Finn', 
            'The annotated Huckleberry Finn : Adventures of Huckleberry Finn',
            "The annotated Huckleberry Finn : Adventures of Huckleberry Finn (Tom Sawyer's comrade)",
            'Tom Sawyer. Huckleberry Finn.',
        ],
            sorted(huck2[0.5]))

        eq_([
            'Adventures of Huckleberry Finn : a case study in critical controversy', 
            'Adventures of Huckleberry Finn : an authoritative text, contexts and sources, criticism', 'Tom Sawyer and Huckleberry Finn'
        ],
            sorted(huck2[0.25]))

        eq_(['Mark Twain : four complete novels.', 'Mississippi writings'],
            sorted(huck2[0]))


        alice = self._arrange_by_confidence_level(
            "Alice's Adventures in Wonderland",

            'The nursery "Alice"',
            'Alice in Wonderland',
            'Alice in Zombieland',
            'Through the looking-glass and what Alice found there',
            "Alice's adventures under ground",
            "Alice in Wonderland &amp; Through the looking glass",
            "Michael Foreman's Alice's adventures in Wonderland",
            "Alice in Wonderland : comprising the two books, Alice's adventures in Wonderland and Through the looking-glass",
        )

        eq_([], alice[0.8])
        eq_(['Alice in Wonderland', 
             "Alice in Wonderland : comprising the two books, Alice's adventures in Wonderland and Through the looking-glass", 
             "Alice's adventures under ground",
             "Michael Foreman's Alice's adventures in Wonderland"],
            sorted(alice[0.5]))

        eq_(['Alice in Wonderland &amp; Through the looking glass',
             "Alice in Zombieland"],
            sorted(alice[0.25]))

        eq_(['The nursery "Alice"',
             'Through the looking-glass and what Alice found there'],
            sorted(alice[0]))
