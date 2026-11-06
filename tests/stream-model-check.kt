fun main() {
    val legacy = parseFinding("old\tready\tCollinearPoints: A, B, C\told.geo\t")!!
    check(legacy.topic == "CollinearPoints" && legacy.beauty == null)
    val fresh = parseFinding("new\tready\tEqualLineSegments: A, B, C, D\tnew.geo\t\t4051.612345\tEqualLineSegments")!!
    check(fresh.beauty == 4051.612345 && beautyLabel(fresh.beauty) == "4051.61")
    check(parseFinding("bad") == null)
    check(parseFinding("x\tready\tUnknown: A\tx.geo\t\tNaN\tUnknown")!!.beauty == null)
    val a = fresh.copy(id = "a", beauty = -5.0)
    val b = fresh.copy(id = "b", beauty = 0.0)
    val c = fresh.copy(id = "c", beauty = 12.0)
    check(mergeFindingOrder(listOf("b"), listOf(a, b, c)) == listOf("b", "a", "c"))
    check(mergeFindingOrder(listOf("b", "a"), listOf(c, a, b)) == listOf("b", "a", "c"))
    check(mergeFindingOrder(listOf("b", "a"), listOf(a, c)) == listOf("a", "c"))
    val sorted = sortByBeauty(listOf(a, b, legacy, c))
    check(sorted == listOf("c", "b", "a", "old"))
    // New high-score findings append instead of continuously shuffling a sorted list.
    check(mergeFindingOrder(sorted, listOf(a, b, legacy, c, fresh.copy(id = "extra", beauty = 1e6))).last() == "extra")
    check(topicLabel("CollinearPoints") == "Коллинеарность")
    check(topicLabel("CustomTheorem") == "CustomTheorem")
    println("STREAM_MODEL_OK: legacy/new metadata, missing scores, topics, stable arrival order and explicit beauty sorting")
}
