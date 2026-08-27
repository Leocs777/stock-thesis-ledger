    const navButtons = [...document.querySelectorAll("[data-page]")];
    const views = [...document.querySelectorAll("[data-view]")];
    const titles = { cover: "Cover", documentation: "Documentation", library: "Library" };

    function selectPage(name, updateUrl = true) {
      const selected = titles[name] ? name : "cover";
      navButtons.forEach(button => button.classList.toggle("active", button.dataset.page === selected));
      views.forEach(view => view.classList.toggle("active", view.dataset.view === selected));
      document.getElementById("pageTitle").textContent = titles[selected];
      if (updateUrl) history.replaceState(null, "", `?page=${selected}`);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    navButtons.forEach(button => button.addEventListener("click", () => selectPage(button.dataset.page)));
    selectPage(new URLSearchParams(location.search).get("page") || "cover", false);
