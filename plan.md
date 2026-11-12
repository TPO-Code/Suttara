# Suttara
pyside6 application to learn the Mahāsatipaṭṭhāna Sutta

## side panel shows a list of user curated sections
    
    a section will be a selection of the text in Pali , english, text, wread Pali by a human, chanted Pali by a human and a user notes section
    hear we can modify the position of the section in the list, add or insert a insert section after/before the currently selected section, rename and delete section
    we can save he entire project using the applications menu. the save will be in this projects repo directory with a fixed name

## main body - section editor and viewer
    
    hear we can add:
    
    - an english text section by supplying a text file name and line and coloumn for both start and end of the section of the text
    - a Pali text section by supplying a text file name and line and coloumn for both start and end of the section of the text
    - a link or embedded player to the audio of the human wread Pali audio, given a file name and start and end times
    - a link or embedded player to the audio of the human wread english audio, given a file name and start and end times
    - a link or embedded player to the youtube video segment of S.N.Goenka Pali chanting audio, given a file name and start and end times

### notes

for video from youtube, we will prefer an embedded player that can stream the video some how rather than openin a web browser window, and restrict to the given segment start and end time and include a loop option.
for audio, similar to the video we will prefer a seekable, pauseable embedded player. showing the waveform would also be a nice touch and help a use to navigate the audio segment.