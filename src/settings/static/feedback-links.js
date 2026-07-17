const graphLinkObserver = new MutationObserver(() => {
  document.querySelectorAll(".review-item:not([data-graph-link])").forEach(card => {
    card.dataset.graphLink = "true";
    const link = document.createElement("a");
    link.className = "button-link secondary";
    link.textContent = "그래프로 보기";
    link.href = `/search-feedback/${encodeURIComponent(card.dataset.searchId)}`;
    link.addEventListener("click", () => {
      fetch(`/api/search-feedback/${encodeURIComponent(card.dataset.searchId)}/behavior`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({action: "follow_graph", file_path: null, position: null}),
        keepalive: true,
      }).catch(() => {});
    });
    card.insertBefore(link, card.querySelector(".review-paths"));
  });
});
graphLinkObserver.observe(document.getElementById("search-events"), {childList: true});
