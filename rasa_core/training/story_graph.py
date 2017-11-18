from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import logging
import random
from collections import deque, defaultdict
import typing
from rasa_core.domain import Domain
from typing import List, Text, Dict, Optional, Tuple

from rasa_core.interpreter import NaturalLanguageInterpreter
from rasa_core import utils


if typing.TYPE_CHECKING:
    from rasa_core.training.dsl import StoryStep, Story

logger = logging.getLogger(__name__)


class StoryGraph(object):
    def __init__(self, story_steps, story_end_checkpoints=None):
        # type: (List[StoryStep]) -> None
        self.story_steps = story_steps
        self.step_lookup = {s.id: s for s in self.story_steps}
        ordered_ids, cyclic_edges = StoryGraph.order_steps(story_steps)
        self.ordered_ids = ordered_ids
        self.cyclic_edge_ids = cyclic_edges
        if story_end_checkpoints:
            self.story_end_checkpoints = story_end_checkpoints
        else:
            self.story_end_checkpoints = []

    def ordered_steps(self):
        # type: () -> List[StoryStep]
        """Returns the story steps ordered by topological order of the DAG."""

        return [self.get(step_id) for step_id in self.ordered_ids]

    def cyclic_edges(self):
        # type: () -> List[Tuple[Optional[StoryStep], Optional[StoryStep]]]
        """Returns the story steps ordered by topological order of the DAG."""

        return [(self.get(source), self.get(target))
                for source, target in self.cyclic_edge_ids]

    def with_cycles_removed(self):
        from rasa_core.training.dsl import Checkpoint

        story_end_checkpoints = self.story_end_checkpoints[:]
        cyclic_edges = self.cyclic_edges()
        # we need to remove the start steps and replace them with steps ending
        # in a special end checkpoint
        steps_to_be_removed = {start.id for start, _ in cyclic_edges}
        story_steps = [s
                       for s in self.story_steps
                       if s.id not in steps_to_be_removed]

        # add changed start steps again
        for s, e in cyclic_edges:
            cid = Checkpoint(utils.generate_id("CYCLE_"))
            story_end_checkpoints.append(cid.name)

            modified_start = s.create_copy(use_new_id=True)
            modified_start.end_checkpoint = cid
            story_steps.append(modified_start)

            modified_end = e.create_copy(use_new_id=True)
            modified_end.start_checkpoint = cid
            story_steps.append(modified_end)

        return StoryGraph(story_steps, story_end_checkpoints)

    def get(self, step_id):
        # type: (Text) -> Optional[StoryStep]
        """Looks a story step up by its id."""

        return self.step_lookup.get(step_id)

    def build_stories(self,
                      domain,
                      max_number_of_trackers=2000):
        # type: (Domain, NaturalLanguageInterpreter, bool, int) -> List[Story]
        """Build the stories of a graph."""
        from rasa_core.training.dsl import STORY_START, Story

        active_trackers = {STORY_START: [Story()]}
        rand = random.Random(42)

        for step in self.ordered_steps():
            if step.start_checkpoint_name() in active_trackers:
                # these are the trackers that reached this story step
                # and that need to handle all events of the step
                incoming_trackers = active_trackers[
                    step.start_checkpoint_name()]

                # TODO: we can't use tracker filter here to filter for
                #       checkpoint conditions since we don't have trackers.
                #       this code should rather use the code from the dsl.

                if max_number_of_trackers is not None:
                    incoming_trackers = utils.subsample_array(
                            incoming_trackers, max_number_of_trackers, rand)

                events = step.explicit_events(domain)
                # need to copy the tracker as multiple story steps might
                # start with the same checkpoint and all of them
                # will use the same set of incoming trackers
                if events:
                    trackers = [Story(tracker.story_steps + [step])
                                for tracker in incoming_trackers]
                else:
                    trackers = []  # small optimization

                # update our tracker dictionary with the trackers that handled
                # the events of the step and that can now be used for further
                # story steps that start with the checkpoint this step ended on
                if step.end_checkpoint_name() not in active_trackers:
                    active_trackers[step.end_checkpoint_name()] = []
                active_trackers[step.end_checkpoint_name()].extend(trackers)

        return active_trackers[None]

    def as_story_string(self):
        story_content = ""
        for step in self.story_steps:
            story_content += step.as_story_string(flat=False)
        return story_content

    @staticmethod
    def order_steps(story_steps):
        # type: (List[StoryStep]) -> Deque[Text]
        """Topological sort of the steps returning the ids of the steps."""

        checkpoints = StoryGraph._group_by_start_checkpoint(story_steps)
        graph = {s.id: [other.id
                        for other in checkpoints[s.end_checkpoint_name()]]
                 for s in story_steps}
        return StoryGraph.topological_sort(graph)

    @staticmethod
    def _group_by_start_checkpoint(story_steps):
        # type: (List[StoryStep]) -> Dict[Text, List[StoryStep]]
        """Returns all the start checkpoint of the steps"""

        checkpoints = defaultdict(list)
        for step in story_steps:
            checkpoints[step.start_checkpoint_name()].append(step)
        return checkpoints

    @staticmethod
    def topological_sort(graph):
        """Creates a topsort of a directed graph. This is an unstable sorting!

        The function returns the sorted nodes as well as the edges that need
        to be removed from the graph to make it acyclic (and hence, sortable).

        The graph should be represented as a dictionary, e.g.:

        >>> example_graph = {
        ...         "a": ["b", "c", "d"],
        ...         "b": [],
        ...         "c": ["d"],
        ...         "d": [],
        ...         "e": ["f"],
        ...         "f": []}
        >>> StoryGraph.topological_sort(example_graph)
        (deque([u'e', u'f', u'a', u'c', u'd', u'b']), [])
        """
        GRAY, BLACK = 0, 1
        ordered = deque()
        unprocessed = set(graph)
        visited_nodes = {}

        removed_edges = set()

        def dfs(node):
            visited_nodes[node] = GRAY
            for k in graph.get(node, ()):
                sk = visited_nodes.get(k, None)
                if sk == GRAY:
                    removed_edges.add((node, k))
                    continue
                if sk == BLACK:
                    continue
                unprocessed.discard(k)
                dfs(k)
            ordered.appendleft(node)
            visited_nodes[node] = BLACK

        while unprocessed:
            dfs(unprocessed.pop())
        return ordered, removed_edges
