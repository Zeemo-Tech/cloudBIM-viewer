"""Preserve observed contact-fragment identity across Step 06 reconciliation."""
import numpy as np
from . import rebar_fragment_terminals


def finalize_fragment_terminals(context, inventory, *, workers=1):
    original_removed = np.asarray(context.terminal_removed)>0
    if np.any(original_removed & ((context.complete_class==3)|(context.complete_instance>0))):
        raise ValueError('设计归属恢复了合并前剔除点，本轮不发布')
    labels = np.asarray(context.terminal_fragment)
    rows = np.flatnonzero(labels>0)
    ordered = rows[np.argsort(labels[rows],kind='stable')]
    ids, offsets = np.unique(labels[ordered],return_index=True)
    # A removed point may inherit a display ID only from its own surviving
    # observed fragment; unresolved fragments stay unassigned in the comparison.
    for i, fid in enumerate(ids):
        source = ordered[offsets[i]:offsets[i+1] if i+1<len(ids) else len(ordered)]
        cut = source[original_removed[source]]
        alive = source[(context.complete_class[source]==3)&(context.complete_instance[source]>0)]
        if not len(cut) or len(alive)<12: continue
        values, counts = np.unique(context.complete_instance[alive],return_counts=True)
        best = int(values[np.argmax(counts)])
        if counts.max()<.8*len(alive): continue
        segment_values, segment_counts = np.unique(context.complete_segment[alive[context.complete_instance[alive]==best]],return_counts=True)
        context.terminal_previous_instance[cut] = best
        context.terminal_previous_segment[cut] = segment_values[np.argmax(segment_counts)]
    groups = [{**group,'rows':group['rows'][context.complete_class[group['rows']]==3]}
              for group in getattr(context,'terminal_fragment_groups',[])]
    selected, post_labels, report = rebar_fragment_terminals.review_fragment_terminals(context,groups,inventory,workers=workers)
    new = (labels==0)&(post_labels>0)
    if np.any(new):
        labels[new] = post_labels[new] + int(labels.max())
    selected &= context.complete_class==3
    context.terminal_previous_instance[selected] = context.complete_instance[selected]
    context.terminal_previous_segment[selected] = context.complete_segment[selected]
    context.terminal_removed[selected] = 1
    context.terminal_reason[selected] = 3
    context.complete_class[selected] = 4
    context.complete_instance[selected] = 0
    context.complete_segment[selected] = 0
    context.complete_confidence[selected] = 0
    return dict(premerge=getattr(context,'terminal_premerge_report',{}),postmergeFragments=report,
        premergeRemovedPointCount=int(original_removed.sum()),postmergeFragmentRemovedPointCount=int(selected.sum()),
        fragmentCount=int(len(np.unique(labels[labels>0]))), reasonNames={'0':'unchanged','1':'final_instance_end','2':'premerge_fragment_contact','3':'postmerge_fragment_contact'},
        comparisonMeaning='restore peeled source points for display; final-owner IDs only from surviving observed fragment support')
