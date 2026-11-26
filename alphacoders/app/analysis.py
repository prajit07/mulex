from collections import defaultdict, deque
from datetime import timedelta
from functools import lru_cache

import networkx as nx
import numpy as np
from sklearn.ensemble import IsolationForest

from .schema import Dataset

RAPID_WINDOW = timedelta(minutes=15)
CYCLE_WINDOW = timedelta(hours=72)
MAX_CYCLE_LENGTH = 5
MAX_CYCLES = 500
PASS_THROUGH = 0.5  
INDICATOR_MAXIMUM = {
    "Rapid onward transfers": 30,
    "Linked complaints": 25,
    "Multiple funding sources": 8,
    "Shared device": 8,
    "Layering chain": 6,
    "Complaint-linked counterparties": 6,
    "Peer anomaly": 6,
    "Distribution to multiple recipients": 4,
    "Circular fund flow": 4,
    "Departure from account history": 3,
}


def match_credits(rows, account):
    """Apply debits to recent credits once, retaining the unspent lots and their senders."""
    credits = deque()
    rapid = 0
    # A common timestamp gives no evidence that a credit preceded a debit.
    for tx in sorted(rows, key=lambda t: (t.timestamp, t.receiver == account, t.id)):
        while credits and tx.timestamp - credits[0][0] > RAPID_WINDOW:
            credits.popleft()
        if tx.receiver == account:
            credits.append([tx.timestamp, tx.amount_paise, tx.sender])
        else:
            remaining = tx.amount_paise
            while remaining and credits:
                use = min(remaining, credits[0][1])
                remaining -= use
                rapid += use
                credits[0][1] -= use
                if credits[0][1] == 0:
                    credits.popleft()
    return rapid, credits


def available_credits(rows, account, timestamp):
    # Include earlier debits/credits while matching: an earlier debit can spend an
    # older lot even if that lot has since aged out of the authorisation window.
    _, remaining = match_credits((t for t in rows if t.timestamp < timestamp), account)
    recent = [lot for lot in remaining if timestamp - lot[0] <= RAPID_WINDOW]
    return sum(lot[1] for lot in recent), len({lot[2] for lot in recent})


def transfer_features(rows, account):
    rapid, _ = match_credits(rows, account)
    incoming = sum(t.amount_paise for t in rows if t.receiver == account)
    outgoing = sum(t.amount_paise for t in rows if t.sender == account)
    fan_in = {t.sender for t in rows if t.receiver == account}
    fan_out = {t.receiver for t in rows if t.sender == account}
    history = [tx.amount_paise for tx in rows[:-1]]
    median = float(np.median(history)) if len(history) >= 5 else 0.0
    last_amount_ratio = rows[-1].amount_paise / median if median else 0.0
    return dict(
        incoming=incoming,
        outgoing=outgoing,
        rapid=rapid,
        latest_amount_ratio=round(last_amount_ratio, 2),
        fan_in=len(fan_in),
        fan_out=len(fan_out),
        count=len(rows),
    )


class Analysis:
    def __init__(self, dataset):
        self.data = dataset
        self.accounts = {a.id: a for a in dataset.accounts}
        self.events = defaultdict(list)
        self.complaints = defaultdict(list)
        self.devices = defaultdict(list)
        self.edges = defaultdict(list)
        self.graph = nx.DiGraph()
        self.graph.add_nodes_from(self.accounts)
        for tx in sorted(dataset.transactions, key=lambda t: (t.timestamp, t.id)):
            self.events[tx.sender].append(tx)
            self.events[tx.receiver].append(tx)
            self.edges[tx.sender, tx.receiver].append(tx)
            self.graph.add_edge(tx.sender, tx.receiver)
        for c in dataset.complaints:
            self.complaints[c.account].append(c)
        for a in dataset.accounts:
            if a.device:
                self.devices[a.device].append(a.id)
        self.features = {
            key: transfer_features(self.events[key], key) for key in self.accounts
        }
        vectors = [
            [
                np.log1p(v["incoming"]),
                np.log1p(v["outgoing"]),
                v["fan_in"],
                v["fan_out"],
                v["rapid"] / max(1, v["incoming"]),
            ]
            for v in self.features.values()
        ]
        self.peer_model = len(vectors) >= 12 and len({tuple(v) for v in vectors}) >= 4
        anomalies = [0.0] * len(vectors)
        if self.peer_model:
            model = IsolationForest(n_estimators=64, random_state=42, n_jobs=1)
            anomalies = np.clip(
                -model.fit(vectors).decision_function(vectors) * 30, 0, 6
            )
        self.pass_through = {
            key: f["rapid"] / f["incoming"] if f["incoming"] else 0.0
            for key, f in self.features.items()
        }
        self.cycles = self.find_cycles()
        self.scores = {
            key: self.score(key, float(value))
            for key, value in zip(self.accounts, anomalies)
        }
        ranked = {key for key, score in self.scores.items() if score["score"] >= 40}
        related = self.graph.subgraph(ranked).to_undirected()
        for members in self.devices.values():
            included = sorted(ranked.intersection(members))
            related.add_edges_from(zip(included, included[1:]))
        self.clusters = [
            sorted(nodes)
            for nodes in nx.connected_components(related)
            if len(nodes) > 1
        ]
        self.clusters.sort(key=lambda nodes: (-len(nodes), nodes))
        self.incidents = self.group_incidents()
        self.incident_of = {
            account: incident["id"]
            for incident in self.incidents
            for account in incident["accounts"]
        }

    def find_cycles(self):
        """Short directed rings whose transfers occur in order within CYCLE_WINDOW."""
        found = []
        rings = nx.simple_cycles(self.graph, length_bound=MAX_CYCLE_LENGTH)
        for count, ring in enumerate(rings):
            if count >= MAX_CYCLES:
                break
            if len(ring) < 2:
                continue
            path = self.ordered_ring(ring)
            if path:
                found.append(path)
        found.sort(key=lambda c: (c["start"], c["members"]))
        return found

    def ordered_ring(self, ring):
        """Earliest in-order traversal of the ring, trying every starting account."""
        best = None
        for shift in range(len(ring)):
            members = ring[shift:] + ring[:shift]
            hops = list(zip(members, members[1:] + members[:1]))
            for first in self.edges[hops[0]]:
                if best and first.timestamp >= best[0]:
                    break
                chain, current = [first], first.timestamp
                for edge in hops[1:]:
                    nxt = next(
                        (t for t in self.edges[edge] if t.timestamp > current), None
                    )
                    if nxt is None or nxt.timestamp - first.timestamp > CYCLE_WINDOW:
                        break
                    chain.append(nxt)
                    current = nxt.timestamp
                else:
                    best = (first.timestamp, members, chain, current)
                    break
        if best is None:
            return None
        start, members, chain, end = best
        return dict(
            members=members,
            transfers=[t.id for t in chain],
            start=start.isoformat(),
            hours=round((end - start).total_seconds() / 3600, 2),
        )

    def role(self, key):
        f, share = self.features[key], self.pass_through[key]
        if share >= PASS_THROUGH:
            return "Collector" if f["fan_in"] >= 3 else "Layering intermediary"
        senders = {t.sender for t in self.events[key] if t.receiver == key}
        if (
            f["incoming"]
            and f["outgoing"] < 0.2 * f["incoming"]
            and any(self.pass_through[s] >= PASS_THROUGH for s in senders)
        ):
            return "Cash-out endpoint"
        return None

    def group_incidents(self):
        """Group complaints whose reported accounts connect through suspicious accounts."""
        suspicious = {
            k for k, s in self.scores.items() if s["score"] >= 35 or self.complaints[k]
        }
        linked = nx.Graph()
        linked.add_nodes_from(suspicious)
        linked.add_edges_from(
            (a, b) for a, b in self.graph.edges if a in suspicious and b in suspicious
        )
        for members in self.devices.values():
            included = sorted(suspicious.intersection(members))
            linked.add_edges_from(zip(included, included[1:]))
        # Suspicious accounts that pay the same beneficiary are one candidate network.
        beneficiaries = []
        for account in sorted(self.accounts):
            payers = sorted(
                p for p in self.graph.predecessors(account) if p in suspicious
            )
            if len(payers) >= 2 and account not in suspicious:
                linked.add_edges_from(zip(payers, payers[1:]))
                beneficiaries.append((account, payers))
        transfers = {t.id: t for t in self.data.transactions}
        groups = []
        for nodes in nx.connected_components(linked):
            filings = sorted(
                (c for n in nodes for c in self.complaints[n]),
                key=lambda c: (c.timestamp, c.id),
            )
            if not filings:
                continue
            value = sum(
                transfers[c.transfer].amount_paise
                for c in filings
                if c.transfer in transfers
            )
            groups.append(
                dict(
                    complaints=[c.id for c in filings],
                    reported_accounts=sorted({c.account for c in filings}),
                    accounts=sorted(nodes),
                    common_beneficiaries=[
                        dict(account=b, payers=payers)
                        for b, payers in beneficiaries
                        if nodes.issuperset(payers)
                    ],
                    categories=sorted({c.category for c in filings}),
                    reported_value=value,
                    first=filings[0].timestamp.isoformat(),
                    last=filings[-1].timestamp.isoformat(),
                    max_score=max(self.scores[n]["score"] for n in nodes),
                )
            )
        groups.sort(key=lambda g: (-len(g["complaints"]), g["first"]))
        for index, group in enumerate(groups, 1):
            group["id"] = f"INC-{index:03d}"
        return groups

    def cluster_summary(self):
        summaries = []
        for index, members in enumerate(self.clusters, 1):
            inside = set(members)

            def flow(a, b):
                return dict(
                    sender=a,
                    receiver=b,
                    amount_paise=sum(t.amount_paise for t in self.edges[a, b]),
                    count=len(self.edges[a, b]),
                )

            flows = [flow(a, b) for a, b in self.graph.subgraph(members).edges]
            # Direct counterparties give the investigator the funding sources and exits.
            outside = defaultdict(int)
            for m in members:
                for n in self.graph.predecessors(m):
                    if n not in inside:
                        outside[n] += sum(t.amount_paise for t in self.edges[n, m])
                for n in self.graph.successors(m):
                    if n not in inside:
                        outside[n] += sum(t.amount_paise for t in self.edges[m, n])
            context = sorted(outside, key=lambda n: (-outside[n], n))[:12]
            boundary = [
                flow(a, b)
                for a, b in self.graph.edges
                if (a in inside and b in context) or (a in context and b in inside)
            ]
            shared = sorted(
                {
                    device
                    for device, ids in self.devices.items()
                    if len(inside.intersection(ids)) > 1
                }
            )
            summaries.append(
                dict(
                    id=f"NET-{index:02d}",
                    members=members,
                    size=len(members),
                    max_score=max(self.scores[m]["score"] for m in members),
                    complaints=sum(len(self.complaints[m]) for m in members),
                    internal_flow_paise=sum(f["amount_paise"] for f in flows),
                    flows=flows,
                    context=context,
                    boundary_flows=boundary,
                    devices=shared,
                    device_links=[
                        sorted(inside.intersection(self.devices[d])) for d in shared
                    ],
                    cycles=[c for c in self.cycles if inside.issuperset(c["members"])],
                )
            )
        return summaries

    def as_of(self, timestamp):
        transfers = [t for t in self.data.transactions if t.timestamp <= timestamp]
        complaints = [c for c in self.data.complaints if c.timestamp <= timestamp]
        if len(transfers) == len(self.data.transactions) and len(complaints) == len(
            self.data.complaints
        ):
            return self
        return Analysis(
            self.data.model_copy(
                update={"transactions": transfers, "complaints": complaints}
            )
        )

    def score(self, key, anomaly):
        f = self.features[key]
        reasons = []

        def add(label, points, evidence):
            if points > 0:
                reasons.append(
                    dict(
                        indicator=label,
                        points=round(points, 1),
                        maximum=INDICATOR_MAXIMUM[label],
                        evidence=evidence,
                    )
                )

        ratio = self.pass_through[key]
        add(
            "Rapid onward transfers",
            30 * ratio,
            f"{ratio:.0%} of incoming value matched to onward payments within 15 minutes",
        )
        if ratio >= PASS_THROUGH:
            upstream = sorted(
                {
                    t.sender
                    for t in self.events[key]
                    if t.receiver == key and self.pass_through[t.sender] >= PASS_THROUGH
                }
            )
            downstream = sorted(
                {
                    t.receiver
                    for t in self.events[key]
                    if t.sender == key and self.pass_through[t.receiver] >= PASS_THROUGH
                }
            )
            if upstream or downstream:
                chain = " → ".join(
                    part
                    for part in (
                        ", ".join(upstream[:3]),
                        key,
                        ", ".join(downstream[:3]),
                    )
                    if part
                )
                add(
                    "Layering chain",
                    6,
                    f"Part of a chain of rapid pass-through accounts: {chain}",
                )
        add(
            "Multiple funding sources",
            min(8, max(0, f["fan_in"] - 1) * 2),
            f"{f['fan_in']} distinct senders",
        )
        add(
            "Distribution to multiple recipients",
            min(4, max(0, f["fan_out"] - 2)),
            f"{f['fan_out']} distinct recipients",
        )
        multiple = f["latest_amount_ratio"]
        if multiple >= 3:
            add(
                "Departure from account history",
                min(3, multiple - 2),
                f"Latest amount is {multiple:.1f} times the median of the account's preceding transfers",
            )
        filings = self.complaints[key]
        add(
            "Linked complaints",
            min(25, 10 * len(filings)),
            ", ".join(c.id for c in filings[:5]),
        )
        neighbors = set(self.graph.predecessors(key)) | set(self.graph.successors(key))
        reported = sorted(n for n in neighbors if self.complaints[n])
        add(
            "Complaint-linked counterparties",
            min(6, len(reported) * 3),
            ", ".join(reported[:5]),
        )
        device = self.accounts[key].device
        peers = sorted(n for n in self.devices.get(device, ()) if n != key)
        add("Shared device", 8 if peers else 0, ", ".join(peers[:5]))
        ring = next((c for c in self.cycles if key in c["members"]), None)
        if ring:
            add(
                "Circular fund flow",
                4,
                " → ".join(ring["members"] + ring["members"][:1])
                + f" completed in {ring['hours']} h",
            )
        add(
            "Peer anomaly",
            anomaly,
            "Isolation Forest on volume, counterparties and rapid flow",
        )
        total = round(sum(r["points"] for r in reasons), 1)
        level = (
            "Critical"
            if total >= 75
            else "High"
            if total >= 55
            else "Review"
            if total >= 35
            else "Low"
        )
        return dict(
            id=key,
            name=self.accounts[key].name,
            score=total,
            level=level,
            reasons=reasons,
            role=self.role(key),
            **f,
            complaints=len(filings),
        )

    def trail(self, root, depth=3, limit=140):
        # Earliest reachable timestamp per (account, hop) dominates later arrivals.
        frontier = {root: None}
        seen, hops = set(), []
        for hop in range(1, depth + 1):
            following = {}
            for key, after in sorted(frontier.items()):
                for tx in self.events[key]:
                    if tx.sender != key or (
                        after is not None and tx.timestamp <= after
                    ):
                        continue
                    if tx.id not in seen:
                        if len(hops) >= limit:
                            return hops, True
                        seen.add(tx.id)
                        hops.append(dict(**tx.model_dump(mode="json"), hop=hop))
                    arrival = following.get(tx.receiver)
                    if tx.receiver != root and (
                        arrival is None or tx.timestamp < arrival
                    ):
                        following[tx.receiver] = tx.timestamp
            frontier = following
        return hops, False

    def sources(self, root, depth=3, limit=140):
        """Trace strictly earlier transfers back towards the origin of funds."""
        frontier = {root: None}
        seen, hops = set(), []
        for hop in range(1, depth + 1):
            preceding = {}
            for key, before in sorted(frontier.items()):
                for tx in reversed(self.events[key]):
                    if tx.receiver != key or (
                        before is not None and tx.timestamp >= before
                    ):
                        continue
                    if tx.id not in seen:
                        if len(hops) >= limit:
                            return hops, True
                        seen.add(tx.id)
                        hops.append(dict(**tx.model_dump(mode="json"), hop=hop))
                    departure = preceding.get(tx.sender)
                    if tx.sender != root and (
                        departure is None or tx.timestamp > departure
                    ):
                        preceding[tx.sender] = tx.timestamp
            frontier = preceding
        return hops, False

    def detail(self, key):
        trail, truncated = self.trail(key)
        origin, origin_truncated = self.sources(key)
        incident = next(
            (i for i in self.incidents if i["id"] == self.incident_of.get(key)), None
        )
        a = self.accounts[key]
        return dict(
            **self.scores[key],
            transactions=[
                t.model_dump(mode="json") for t in reversed(self.events[key])
            ],
            filings=[c.model_dump(mode="json") for c in self.complaints[key]],
            device_peers=[n for n in self.devices.get(a.device, []) if n != key],
            cluster=next((group for group in self.clusters if key in group), []),
            trail=trail,
            trail_truncated=truncated,
            source_trail=origin,
            source_truncated=origin_truncated,
            incident=incident,
            cycles=[c for c in self.cycles if key in c["members"]],
        )


@lru_cache(maxsize=4)
def analyze(serialized: str):
    return Analysis(Dataset.model_validate_json(serialized))
