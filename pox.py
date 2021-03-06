#! /usr/bin/env python2.7

"""Python OpenCV Example (POX)
App class
- Worker process startup
- Main loop with Face/Eye detection
- Optional Listen-and-Repeat speech recognition mode
- Camera and diagnostic view
- User control via keyboard

"""

import sys
import Queue
import time
import os

import cv2

import poxutil
import poxtts
import poxrec
import poxcom
import poxfsm
import poxcv


def make_movie(img_path):
    """Generate an MOV file from a directory of PNG files."""

    # gather file names of frames
    img_files = []
    for (dirpath, dirnames, filenames) in os.walk(img_path):
        img_files.extend(filenames)
        break

    # only consider PNG files
    img_files = [each for each in img_files if each.rfind(".png") > 0]
    if len(img_files) == 0:
        print "No PNG files found!"
        return

    # determine size of frames
    file_path = os.path.join(img_path, img_files[0])
    size = cv2.cv.GetSize(cv2.cv.LoadImage(file_path))

    # build movie from separate frames
    # TODO -- may need to change FPS on different systems
    fps = 15
    movie_path = os.path.join(img_path, "movie.mov")
    video_maker = cv2.VideoWriter(movie_path,
                                  cv2.cv.CV_FOURCC('m', 'p', '4', 'v'),
                                  fps, size)
    if video_maker.isOpened():
        for each in img_files:
            file_path = os.path.join(img_path, each)
            img = cv2.imread(file_path)
            video_maker.write(img)


class App(object):

    # OpenCV (B,G,R) named color dictionary
    # (these are purely arbitrary)
    color = {"black": (0, 0, 0),
             "pink": (192, 0, 192),
             "cyan": (192, 192, 0),
             "gray": (128, 128, 128),
             "white": (255, 255, 255),
             "yellow": (0, 192, 192),
             "green": (0, 192, 0),
             "red": (0, 0, 192),
             "brick": (64, 64, 128),
             "purple": (128, 64, 64),
             "blue": (192, 0, 0)}

    def __init__(self):

        # worker thread stuff
        self.thread_tts = poxtts.TTSDaemon()
        self.thread_rec = poxrec.RECDaemon()
        self.thread_com = poxcom.Com()
        self.event_queue = Queue.Queue()

        # execution stuff
        self.cvx = poxcv.CVMain()
        self.cvsm = poxfsm.SMLoop()
        self.phrase_mgr = poxutil.PhraseManager()
        self.roi = None

        # state stuff best suited to top-level app
        self.b_eyes = True
        self.b_grin = False
        self.s_strikes = ""
        self.phrase = ""
        self.n_z = 0

        # frame capture and recording
        self.record_enable = False
        self.record_ct = 0
        self.record_clip = 0
        self.record_sfps = "???"
        self.record_t0 = 0.0
        self.record_k = 0
        self.record_path = os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "movie")
        self.record_ok = os.path.isdir(self.record_path)

        # scale the face detection ROI
        # will chop a percentage from top/bottom and left/right
        # can only use values in the range 0.0 - 0.5
        self.roi_perc_h = 0.1
        self.roi_perc_w = 0.2

    def check_z(self):
        # timer for output "Z" test
        if self.n_z > 0:
            self.n_z -= 1
            if self.n_z == 0:
                self.external_action(False)

    def reset_fps(self):
        # clear FPS calculation data
        self.record_t0 = time.time()
        self.record_k = 0
        self.record_sfps = "???"

    def update_fps(self):
        # recalculate frames-per-second every 100 frames
        self.record_k += 1
        if self.record_k == 100:
            t1 = time.time()
            tx = t1 - self.record_t0
            self.record_t0 = t1
            self.record_sfps = "{:.1f}".format(float(self.record_k) / tx)
            self.record_k = 0

    def record_frame(self, frame, name_prefix):
        """Record frames to sequentially numbered files if enabled."""
        if self.record_ok and self.record_enable:
            file_name = name_prefix
            if file_name is None or len(file_name) == 0:
                file_name = "frame"
            file_name += "_"
            file_name += str(self.record_clip).zfill(2)
            file_name += "_"
            file_name += str(self.record_ct).zfill(5)
            file_name += ".png"
            file_path = os.path.join(self.record_path, file_name)
            self.record_ct += 1
            cv2.imwrite(file_path, frame)

    def get_roi(self, h, w):
        """
        Given source image dimensions, returns X and Y
        coordinates for region-of-interest based on
        percentages for chopping top/bottom and left/right.

        :param h: Height of source image
        :param w: Width of source image
        :return: (y0, y1, x0, x1)
        """
        rh = self.roi_perc_h
        rw = self.roi_perc_w
        return int(rh * h), int((1 - rh) * h), int(rw * w), int((1 - rw) * w)

    def external_action(self, flag, data=None):
        """
        Sends command to an external serial device.
        """
        if flag:
            # configure digital pin as output and turn on
            self.thread_com.post_cmd("dig0_cfg", "0")
            self.thread_com.post_cmd("dig0_io", "1")
            print "EXT ON", data
        else:
            # turn off digital pin and configure as input
            self.thread_com.post_cmd("dig0_io", "0")
            self.thread_com.post_cmd("dig0_cfg", "1")
            print "EXT OFF"

    @staticmethod
    def show_help():
        # press '?' while monitor has focus
        # to see this menu
        print "? - Display help."
        print "1 - Toggle eye detection."
        print "2 - Toggle smile detection."
        print "g - Go. Restarts monitoring."
        print "h - Halt. Stops monitoring and any external action."
        print "L - Start scripted speech mode.  Only valid when monitoring."
        print "M - Make MOV movie file from recorded video frames."
        print "s - (Test) Say next phrase from file."
        print "r - (Test) Recognize phrase that was last spoken."
        print "Q - Quit."
        print "V - Toggle video recording."
        print "Z - (Test) Activate external output for half-second."
        print "ESC - Quit."

    def show_monitor_window(self, img, boxes, sfps):
        # update display items
        status_color = App.color[self.cvsm.snapshot["color"]]
        s_label = self.cvsm.snapshot["label"]

        h, w = img.shape[:2]
        h1, h2, w1, w2 = self.get_roi(h, w)

        # draw face boxes and face ROI
        img_final = img
        for each in boxes:
            pt1 = (each[0][0] + w1, each[0][1] + h1)
            pt2 = (each[1][0] + w1, each[1][1] + h1)
            cv2.rectangle(img_final, pt1, pt2, App.color["green"])
        cv2.rectangle(img_final, (w1, h1), (w2, h2), App.color["cyan"])

        wn = 54  # width of status boxes
        hn = 20  # height of status boxes

        # draw status label in upper left
        # along with status color
        cv2.rectangle(img_final, (0, 0), (wn, hn), status_color,
                      cv2.cv.CV_FILLED)
        cv2.rectangle(img_final, (0, 0), (wn, hn), App.color["white"])
        cv2.putText(img_final, s_label, (10, 14), cv2.FONT_HERSHEY_PLAIN, 1.0,
                    App.color["white"], 2)

        # mode state icon box
        cv2.rectangle(img_final, (0, hn), (wn, hn * 2), App.color["purple"],
                      cv2.cv.CV_FILLED)
        cv2.rectangle(img_final, (0, hn), (wn, hn * 2), App.color["white"])

        # strike count display
        speech_mode_color = self.cvsm.psm.snapshot["color"]
        cv2.rectangle(img_final, (0, hn * 2), (wn, hn * 3),
                      App.color[speech_mode_color], cv2.cv.CV_FILLED)
        cv2.rectangle(img_final, (0, hn * 2), (wn, hn * 3),
                      App.color["white"])
        cv2.putText(img_final, self.s_strikes, (10, hn * 2 + 14),
                    cv2.FONT_HERSHEY_PLAIN, 1.0, App.color["white"], 2)

        # frames per second and recording status
        fps_color = "red" if self.record_enable is True else "black"
        cv2.rectangle(img_final, (0, hn * 3), (wn, hn * 4),
                      App.color[fps_color], cv2.cv.CV_FILLED)
        cv2.rectangle(img_final, (0, hn * 3), (wn, hn * 4),
                      App.color["white"])
        cv2.putText(img_final, sfps, (10, hn * 3 + 14),
                    cv2.FONT_HERSHEY_PLAIN, 1.0, App.color["white"], 2)

        # draw speech recognition progress (timeout) bar if active
        # just a black rectangle that gets filled with gray blocks
        # there's a yellow warning bar at ideal timeout time
        x = int(self.cvsm.snapshot["prog"])
        if x > 0:
            rec_sec = int(poxfsm.SMPhrase.REC_TIMEOUT_SEC)
            wb = 10
            x1 = wn
            x2 = wn + (rec_sec - x) * wb
            x3 = wn + rec_sec * wb
            xtrg = x1 + 12 * wb  # see poxrec.py
            cv2.rectangle(img_final, (x1, 0), (x2, hn), App.color["gray"],
                          cv2.cv.CV_FILLED)
            cv2.rectangle(img_final, (x2, 0), (x3, hn), App.color["black"],
                          cv2.cv.CV_FILLED)
            cv2.line(img_final, (xtrg, 0), (xtrg, hn), App.color["yellow"])
            cv2.rectangle(img_final, (x1, 0), (x3, hn), App.color["white"])

        # draw eye detection state indicator (pair of eyes)
        if self.b_eyes:
            e_y = 28
            e_x = 8
            e_dx = 8
            cv2.circle(img_final, (e_x, e_y), 3, App.color["white"],
                       cv2.cv.CV_FILLED)
            cv2.circle(img_final, (e_x + e_dx, e_y), 3, App.color["white"],
                       cv2.cv.CV_FILLED)
            cv2.circle(img_final, (e_x, e_y), 1, App.color["black"],
                       cv2.cv.CV_FILLED)
            cv2.circle(img_final, (e_x + e_dx, e_y), 1, App.color["black"],
                       cv2.cv.CV_FILLED)

        # draw grin detection state indicator (curve like a grin)
        if self.b_grin:
            g_x = 34
            g_y = 28
            cv2.ellipse(img_final, (g_x, g_y), (5, 3), 0, 0, 180,
                        App.color["white"], 2)

        # record frame if enabled and update monitor
        self.record_frame(img_final, "img")
        cv2.imshow("POX Monitor", img_final)

    def wait_and_check_keys(self, event_list):
        result = True
        # this is where all key input comes from
        # key press that affects state machine will be stuffed in event
        # that event will be handled at next iteration
        key = cv2.waitKey(1)
        if key == 27 or key == ord('Q'):
            # esc or Q to quit
            result = False
        elif key == ord('1'):
            # toggle eye detection
            self.b_eyes = not self.b_eyes
        elif key == ord('2'):
            # toggle grin detection
            self.b_grin = not self.b_grin
        elif key in poxfsm.USER_KEYS:
            event_list.append(poxfsm.SMEvent(poxfsm.SMEvent.E_KEY, key))
        elif key == ord('s'):
            if self.cvsm.is_idle():
                # test retrieval and speaking of next phrase
                # it will be saved for manual recognition step
                self.phrase = self.phrase_mgr.next_phrase()
                self.thread_tts.post_cmd('say', self.phrase)
        elif key == ord('r'):
            if self.cvsm.is_idle():
                print "REC Test:", self.phrase
                self.thread_rec.post_cmd('hear', self.phrase)
        elif key == ord('?'):
            App.show_help()
        elif key == ord('Z'):
            self.n_z = 10
            self.external_action(True)
        elif key == ord('V'):
            if self.record_ok:
                if self.record_enable is True:
                    self.record_enable = False
                else:
                    # new clip, reset frame ct
                    self.record_enable = True
                    self.record_clip += 1
                    self.record_ct = 0
        elif key == ord('M'):
            print "Begin making movie"
            make_movie(self.record_path)
            print "Finished"
            self.reset_fps()
        return result

    def loop(self):
        """
        Main application loop:
        - Do frame acquisition
        - Collect events
        - Apply events to state machine
        - Update display
        - Check keyboard input
        """

        # need a 0 as argument
        vcap = cv2.VideoCapture(0)
        if not vcap.isOpened():
            print "Camera Device failed to open."
            return False

        # this may need to change depending on camera
        # (seemed like a good value for MacBook Pro)
        img_scale = 0.5

        # this must persist between iterations
        events = []

        self.reset_fps()

        while True:

            # process images frame-by-frame
            # grab image, downsize, extract ROI, run detection
            # b_found will be result of face/eye/grin detection
            # boxes have data for drawing rectangles for what was detected
            ret, img = vcap.read()
            img_small = cv2.resize(img, (0, 0), fx=img_scale, fy=img_scale)
            h, w = img_small.shape[:2]
            h1, h2, w1, w2 = self.get_roi(h, w)
            imgx = img_small[h1:h2, w1:w2]
            b_found, boxes = self.cvx.detect(imgx, self.b_eyes, self.b_grin)

            # propagate face/eye found event
            if b_found:
                events.append(poxfsm.SMEvent(poxfsm.SMEvent.E_CVOK))

            # poll to see if workers sent any messages
            while not self.event_queue.empty():
                x = self.event_queue.get()
                self.event_queue.task_done()
                stokens = x.split()
                if stokens[0] == poxtts.POX_TTS:
                    events.append(poxfsm.SMEvent(poxfsm.SMEvent.E_SDONE))
                elif stokens[0] == poxrec.POX_REC:
                    if stokens[1] == 'init':
                        # just print out initialization result
                        print " ".join(stokens[2:])
                    elif self.cvsm.is_idle():
                        # probably running a test
                        # so just print message
                        print x
                    else:
                        # second token is 'True' or 'False'
                        # state machine will ack with strike count
                        flag = eval(stokens[1])
                        rdone = poxfsm.SMEvent(poxfsm.SMEvent.E_RDONE, flag)
                        events.append(rdone)
                elif stokens[0] == poxcom.POX_COM:
                    print stokens

            # event list may have worker thread events and detection OK event
            # add any state machine timer events to event list
            # then apply events to state machine
            outputs = []
            events.extend(self.cvsm.check_timers())
            for event in events:
                x = self.cvsm.crank(event)
                outputs.extend(x)
            events = []

            # handle any actions produced by state machine
            for action in outputs:
                assert (isinstance(action, poxfsm.SMEvent))
                if action.code == poxfsm.SMEvent.E_SAY:
                    # issue command to say a phrase
                    self.thread_tts.post_cmd('say', action.data)
                elif action.code == poxfsm.SMEvent.E_SAY_REP:
                    # retrieve next phrase to be repeated
                    # and issue command to say it
                    # phrase is stashed for upcoming recognition step...
                    self.phrase = self.phrase_mgr.next_phrase()
                    self.thread_tts.post_cmd('say', self.phrase)
                elif action.code == poxfsm.SMEvent.E_SRGO:
                    # issue command to recognize a phrase
                    self.thread_rec.post_cmd('hear', self.phrase)
                elif action.code == poxfsm.SMEvent.E_SRACK:
                    # update strike display string
                    # propagate FAIL message if limit reached
                    self.s_strikes = "X" * action.data
                    if action.data == 3:
                        events.append(poxfsm.SMEvent(poxfsm.SMEvent.E_SRFAIL))
                elif action.code == poxfsm.SMEvent.E_XON:
                    self.external_action(True, action.data)
                elif action.code == poxfsm.SMEvent.E_XOFF:
                    self.external_action(False)
                    self.s_strikes = ""

            # update displays
            self.update_fps()
            self.show_monitor_window(img_small, boxes, self.record_sfps)
            self.check_z()

            # final step is to check keys
            # key events will be handled next iteration
            # loop might be terminated here if check returns False
            if not self.wait_and_check_keys(events):
                break

        # loop was terminated
        # be sure any external action is also halted
        self.external_action(False)

        # When everything done, release the capture
        vcap.release()
        cv2.destroyAllWindows()

    def main(self):

        print "*** Python OpenCV Example (POX) ***"
        print "OS: ", sys.platform
        print "EXE:", sys.executable
        if not self.record_ok:
            print "Recording disabled.  Path not found:", self.record_path

        # lazy hard-code for the port settings
        # (used a Keyspan USB-Serial adapter)
        if self.thread_com.open("/dev/cu.USA19H142P1.1", 9600):
            print "Serial Port Opened OK"
        else:
            print "Failure opening serial port!"

        if self.phrase_mgr.load("phrases.txt"):
            print "Phrase File Loaded OK"
        else:
            print "Failure loading phrases!"

        # just look in working folder for cascades
        if self.cvx.load_cascades(path="./"):
            self.thread_tts.start(self.event_queue)
            self.thread_rec.start(self.event_queue)
            self.thread_com.start(self.event_queue)
            self.loop()
        print "DONE"


if __name__ == '__main__':
    app = App()
    app.main()
