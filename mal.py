import requests, ast, warnings, json, qbittorrentapi, time, pickle
from multiprocessing import Process
from multiprocessing.shared_memory import SharedMemory
# for some reason the MAL API returns the 'main_picture' field even without asking for it and it
# contains many instances of '\/' which throw a bunch of warnings on screen, so just supress them
warnings.filterwarnings(action="ignore", category=SyntaxWarning)
NA = "n/a"
CANCELLED = "cancelled"

#################################
# Class Definitions             #
#################################


class MAL:
    ACCESS_TOKEN: str
    REFRESH_TOKEN: str
    last_request: float
    timeout: float = 0.5

    def __init__(self, token_file: str) -> None:
        self.last_request = time.time()
        # https://myanimelist.net/apiconfig/references/api/v2 <-- api docs
        # load MAL API tokens -> see refresh_token.py for getting tokens
        with open(token_file) as f:
            data = json.load(f)
            self.ACCESS_TOKEN = data["access_token"]
            self.REFRESH_TOKEN = data["refresh_token"]

    def send_request(self, url: str) -> dict:
        diff = (time.time() - self.last_request)
        if diff < self.timeout:
            time.sleep(diff)
        response = requests.get(url, headers={"Authorization": f"Bearer {self.ACCESS_TOKEN}"})
        self.last_request = time.time()
        return ast.literal_eval(response.text)  # converts the json response.text field (essentially a string of response.content) into a python dict

    def get_name(self, json_dict: dict) -> str:
        name = json_dict["alternative_titles"]["en"]
        if name == "":
            name = json_dict["title"]
        return name

    def get_info(self, anime_id: int) -> dict:
        url = f"https://api.myanimelist.net/v2/anime/{anime_id}?fields=id,title,alternative_titles,status,num_episodes,mean,related_anime,start_season,genres"
        json_dict = self.send_request(url)
        return json_dict

    def get_val(self, json_data: dict, key: str) -> str:
        try:
            return json_data[key]
        except KeyError:
            return NA

    def search_mal(self, search: str) -> 'list[Show]':
        out = []
        url = f"https://api.myanimelist.net/v2/anime?q={search}&fields=alternative_titles,anime_id"
        json_dict = self.send_request(url)
        index = 0
        for i in json_dict["data"]:
            show = Show(i["node"]["id"], self)
            print(f"  [{index}] : {show}")
            out.append(show)
            index += 1
        return out


class Show:
    name: str                           # name (doh)
    id: int                             # MAL id of this show
    is_completed: bool                  # is this show finished airing
    length: int                         # number of episodes
    rating: float                       # MAL score of this show
    related_shows: 'list[RelatedShow]'  # dict object of 'related_anime' field of json
    start_season: str			# 'spring 2014', 'summer 2023', etc
    genres: []

    def __init__(self, id: int, mal: MAL, load_related: bool = False) -> None:
        self.id = id
        json_data = mal.get_info(id)
        self.name = mal.get_name(json_data)
        self.is_completed = json_data["status"] == "finished_airing"
        self.length = mal.get_val(json_data, "num_episodes")
        mean = mal.get_val(json_data, "mean")
        self.rating = -1 if mean == NA else float(mean)
        self.related_shows = [RelatedShow(i, mal) for i in json_data["related_anime"]] if load_related else []
        try:
            self.start_season = json_data["start_season"]["season"] + " " + str(json_data["start_season"]["year"])
        except KeyError:
            self.start_season = "unknown release season"
        self.genres = []
        try:
            for genre in json_data["genres"]:	# will be an array of form [{id: some_num, name: "string describing genre"}, {id: some_other_num, name: "string describing genre"}]
                self.genres.append(genre["name"])
        except KeyError: # no genres yet
            self.genres = ["No genres yet"]

    def __str__(self) -> str:
        # https://gist.github.com/egmontkob/eb114294efbcd5adb1944c9f3cb5feda
        # https://stackoverflow.com/questions/22125114/how-to-insert-links-in-python
        # https://stackoverflow.com/a/46289463
        info_string = f"{'✓' if self.is_completed else 'X'} : {self.name} | rated {self.rating if self.rating != -1 else NA} | {self.length} episodes | {self.start_season.title()} | {', '.join(self.genres)}"
        url = f"https://myanimelist.net/anime/{self.id}"
        OSC = "\x1b]"   # OSC = operating system command = ESC + ]
        ST = "\x1b\\"   # ST = string terminator = ESC + \
        return f" {OSC}8;;{url}{ST}{info_string}{OSC}8;;{ST}"

    def __lt__(self, i: 'Show') -> bool:
        return i.rating < self.rating

    def related_shows_to_str(self) -> str:
        return "\n  " + '\n  '.join([str(i) for i in self.related_shows])


class RelatedShow(Show):
    relation_type: str

    def __init__(self, json: dict, mal: MAL) -> None:
        super().__init__(json["node"]["id"], mal, False)
        self.relation_type = json["relation_type_formatted"]

    def __str__(self) -> str:
        return f"{super().__str__()} | {self.relation_type}"


class Torrent:
    description_url: str    # link to the torrent description page
    file_url: str           # link to the torrent file itself
    seeders: int            # number of seeders
    size: int               # size of the torrent in bytes
    name: str               # name of the torrent

    def __init__(self, qb_dict) -> None:
        self.description_url = qb_dict["descrLink"]
        self.file_url = qb_dict["fileUrl"]
        self.seeders = qb_dict["nbSeeders"]
        self.size = qb_dict["fileSize"]
        self.name = qb_dict["fileName"]

    def __str__(self) -> str:
        size_scaled = float(self.size)
        count = 0
        while size_scaled > 1024:
            size_scaled /= 1024
            count += 1
        size_unit = ""
        match count:
            case 1: size_unit = "KB"
            case 2: size_unit = "MB"
            case 3: size_unit = "GB"
            case 4: size_unit = "TB"
        size_scaled = round(size_scaled, 2)
        return f"\"{self.name}\" | {size_scaled:.2f} {size_unit} | {self.seeders} seeders"

    def __lt__(self, i: 'Torrent') -> bool:
        return i.seeders < self.seeders


class LoadingBar:
    prefix: str     # text to put before the loading bar
    cycle: 'list[str]' = ["⠋", "⠙", "⠚", "⠞", "⠖", "⠦", "⠴", "⠲", "⠳", "⠓"]
    total: int      # keep track of total tasks we're loading for
    completed: int  # keep track of total number of completed tasks
    memory: SharedMemory    # share number of completed tasks with the loading process

    def __init__(self, prefix: str, total_tasks: int = -1) -> None:
        self.prefix = prefix
        self.total = total_tasks
        self.completed = 0
        self.memory = SharedMemory("loading", True, 5)  # create shared memory for one 4-byte integer and a 1-byte flag

    def loading(self) -> None:
        length = len(self.cycle)
        index = 0
        done = False
        while not done:
            done = int.from_bytes(self.memory.buf[4:], "big") == 1
            percent = ""
            if self.total != -1:
                percent = f" {int.from_bytes(self.memory.buf[:4], 'big') / self.total * 100:.2f}%"
            print(f"\r {self.prefix}{self.cycle[index % length]}{percent}", end="\r", flush=True)
            index += 1
            time.sleep(0.1)
        self.memory.close()

    def start(self) -> None:
        Process(target=self.loading).start()

    def stop(self) -> None:
        self.memory.buf[4:] = int.to_bytes(1, 1, "big")    # set the 'done' flag
        time.sleep(0.2)    # sleep for 200ms to make sure the loading process exits properly before we unlink the shared memory
        self.memory.unlink()
        print(f"\r{' ' * (len(self.prefix) + len(self.cycle[0]) + 9)}", end="\r", flush=True)   # +8 for the ' 100.00%' and +1 for the space before the prefix
        print(f"{self.prefix}done.")

    def update(self) -> None:
        self.completed += 1
        self.memory.buf[:4] = self.completed.to_bytes(4, "big")


class Main:
    CACHE_FILE: str
    qb_client: qbittorrentapi.Client
    mal_client: MAL
    commands: dict = {}
    shows: 'list[Show]' = []
    QBITTORRENT: bool = True

    def __init__(self, cache_file: str, client: MAL) -> None:
        self.CACHE_FILE = cache_file
        self.mal_client = client
        self.commands = {"sm": self.cmd_search_mal,
                         "al": self.cmd_add_to_list,
                         "ad": self.cmd_add_to_list_direct,
                         "rl": self.cmd_remove_from_list,
                         "sl": self.cmd_search_list,
                         "cl": self.cmd_check_list,
                         "re": self.cmd_refresh_list,
                         "vd": self.cmd_view_details,
                         "qb": self.cmd_search_qbittorrent,
                         "qbd": self.cmd_search_qbittorrent_direct,
                         "h": self.cmd_help,
                         "?": self.cmd_help}

        # init qbittorrent api client: https://qbittorrent-api.readthedocs.io/en/latest/
        # requires these environment variables to be set:
        # QBITTORRENTAPI_USERNAME=your_username
        # QBITTORRENTAPI_PASSWORD=your_password
        # QBITTORRENTAPI_HOST=qbittorrent_server_ip:port
        try:
            self.qb_client = qbittorrentapi.Client()
            self.qb_client.auth_log_in()
        except (requests.exceptions.InvalidURL, qbittorrentapi.exceptions.APIConnectionError):
            self.QBITTORRENT = False
            print("qBittorrent server not found, check your environment variables.")
        if not self.QBITTORRENT:
            self.commands["qb"] = lambda: print("qBittorrent server not found, check your environment variables.")

        self.load_list()

    def main(self) -> None:
        while True:
            try:
                self.commands[input(">>> ").lower()]()
            except KeyError:
                print("Invalid command. Type 'h' or '?' to get help.")
            except KeyboardInterrupt:
                self.save_list()
                break

        if self.QBITTORRENT:
            self.qb_client.auth_log_out()    # make sure we log out of our qBittorrent session

    def load_list(self) -> None:
        try:
            with open(self.CACHE_FILE, "rb") as f:
                self.shows += pickle.load(f)     # load cached shows (ie shows that have finished airing)
            print(f"Loaded {len(self.shows)} cached shows.")
        except FileNotFoundError:
            print("No cache file found. If this isn't your first run of the script, make sure you're in the right directory.")

        x = LoadingBar("Refreshing data from myanimelist.net for non 'finished airing' shows... ", len([i for i in self.shows if not i.is_completed]))
        x.start()
        for i in range(len(self.shows)):
            show = self.shows[i]
            if not show.is_completed:   # if show isn't finished airing, refresh its data
                self.shows[i] = Show(show.id, self.mal_client)
                x.update()
        self.shows = sorted(self.shows)
        x.stop()

    def save_list(self) -> None:
        with open(self.CACHE_FILE, "wb") as f:
            pickle.dump(self.shows, f)   # cache shows
        print(f"\nCached {len(self.shows)} shows.")

    def search_list(self) -> 'list[Show]':
        query = input("Enter your search query: ").lower()
        return [show for show in self.shows if query in str(show).lower()]

    def cmd_search_mal(self) -> None:
        self.mal_client.search_mal(input("Search MAL; enter your search query: "))

    def cmd_add_to_list(self) -> None:
        results = self.mal_client.search_mal(input("Add to list; enter your search query: "))
        index = get_int_input("What index do you want to add? ", True)
        if index != CANCELLED:
            show = results[index]
            for i in self.shows:
                if i.id == show.id:
                    print(f"'{show.name}' already in list, cancelling.")
                    return
            self.shows.append(show)
            print(f"Added '{show.name}' to list.")

    def cmd_add_to_list_direct(self) -> None:
        id = get_int_input("What is the MAL id? ", True)
        if id != CANCELLED:
            show = Show(id, self.mal_client)
            for i in self.shows:
                if i.id == show.id:
                    print(f"'{show.name}' already in list, cancelling.")
                    return
            self.shows.append(show)
            print(f"Added '{show.name}' to list.")

    def cmd_remove_from_list(self) -> None:
        shows = self.search_list()
        for i in range(len(shows)):
            print(f"  [{i}] : {shows[i]}")
        index = get_int_input("What index do you want to remove? ", True)
        if index != CANCELLED:
            show = shows[index]
            self.shows.remove(show)
            print(f"Removed '{show.name}' from list.")

    def cmd_search_list(self) -> None:
        query = input("Search list; enter your search query: ").lower()
        for show in self.shows:
            if query in str(show).lower():
                print(show)

    def cmd_check_list(self) -> None:
        print('\n'.join([str(i) for i in self.shows]))

    def cmd_refresh_list(self) -> None:
        x = LoadingBar("Refreshing data from myanimelist.net for your list... ", len(self.shows))
        x.start()
        for i in range(len(self.shows)):
            show = self.shows[i]
            self.shows[i] = Show(show.id, self.mal_client)
            x.update()
        x.stop()

    def cmd_view_details(self) -> None:
        shows = self.search_list()
        for i in range(len(shows)):
            print(f"  [{i}] : {shows[i]}")
        index = get_int_input("What index do you want to view details for? ", True)
        if index != CANCELLED:
            show = shows[index]
            print(f"{show}{show.related_shows_to_str()}")

    def cmd_search_qbittorrent(self) -> None:
        shows = self.search_list()
        finished = [show for show in shows if show.is_completed]
        for i in range(len(finished)):
            print(f"  [{i}] : {finished[i]}")
        index = get_int_input("What index do you want to search for on qBittorrent? ", True)
        if index != CANCELLED:
            show = finished[index]
            query = input("Additional search query (eg `judas`, `batch`, etc): ")
            job = self.qb_client.search_start(f"{show.name}{' ' + query if len(query) > 0 else ''}", "nyaasi", "anime")
            x = LoadingBar("Searching with qBittorrent... ")
            x.start()
            while job.status()[0]["status"] == "Running":
                pass
            x.stop()
            torrents = sorted([Torrent(i) for i in job.results()["results"]])    # sort by number of seeders
            for i in range(10 if len(torrents) >= 10 else len(torrents)):
                print(f"  [{i}] : {torrents[i]}")
            index = get_int_input("What index do you want to download with qBittorrent? ", True)
            if index != CANCELLED:
                torrent = torrents[index]
                self.qb_client.torrents.add([torrent.file_url])
                print("Torrent added successfully.")

    def cmd_search_qbittorrent_direct(self) -> None:
        query = input("Search qbittorrent, enter your query: ")
        job = self.qb_client.search_start(query, "all", "all")
        x = LoadingBar("Searching with qBittorrent... ")
        x.start()
        while job.status()[0]["status"] == "Running":
            pass
        x.stop()
        torrents = sorted([Torrent(i) for i in job.results()["results"]])
        for i in range(10 if len(torrents) >= 10 else len(torrents)):
            print(f"  [{i}] : {torrents[i]}")
        index = get_int_input("What index do you want to download with qBittorrent? ", True)
        if index != CANCELLED:
            torrent = torrents[index]
            self.qb_client.torrents.add([torrent.file_url])
            print("Torrent added successfully.")

    def cmd_help(self) -> None:
        print("Commands are listed here:" +
              "\n  sm : Search MAL directly" +
              "\n  al : Search MAL and add a show to your List" +
              "\n  ad : Add directly via anime id on MAL (use if search isn't working)" +
              "\n  rl : Remove a show from your list" +
              "\n  re : Refetch data for the whole list" +
              "\n  sl : Search your list" +
              "\n  cl : Check status of your list" +
              "\n  vd : View a specific anime in more detail" +
              "\n  qb : Search qBittorrent for torrent links")


def get_int_input(msg: str, cancellable: bool = False) -> int:
    try:
        msgString = f"{msg}{'(or cancel (c)) ' if cancellable else ''}"
        i = input(msgString).lower()
        if cancellable and i == "c":
            return CANCELLED
        x = int(i)
    except ValueError:
        print("Invalid, please enter an integer.")
        x = get_int_input(msg, cancellable)
    return x


if __name__ == "__main__":
    Main("cache.bin", MAL("secret_token.json")).main()
