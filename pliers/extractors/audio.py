''' Extractors that operate on AudioStim inputs. '''

from abc import abstractmethod
from pliers.stimuli.audio import AudioStim
from pliers.stimuli.text import ComplexTextStim
from pliers.extractors.base import Extractor, ExtractorResult
import numpy as np
from scipy import fft

try:
    import librosa
except ImportError:
    librosa = None


class AudioExtractor(Extractor):

    ''' Base Audio Extractor class; all subclasses can only be applied to
    audio. '''
    _input_type = AudioStim


class STFTAudioExtractor(AudioExtractor):

    ''' Short-time Fourier Transform extractor.
    Args:
        frame_size (float): The width of the frame/window to apply an FFT to,
            in seconds.
        hop_size (float): The step size to increment the window by on each
            iteration, in seconds (effectively, the sampling rate).
        freq_bins (list or int): The set of bins or frequency bands to extract
            power for. If an int is passed, this is the number of bins
            returned, with each bin spanning an equal range of frequencies.
            E.g., if bins=5 and the frequency spectrum runs from 0 to 20KHz,
            each bin will span 4KHz. If a list is passed, each element must be
            a tuple or list of lower and upper frequency bounds. E.g., passing
            [(0, 300), (300, 3000)] would compute power in two bands, one
            between 0 and 300Hz, and one between 300Hz and 3KHz.
        spectrogram (bool): If True, plots a spectrogram of the results.

    Notes: code adapted from
    http://stackoverflow.com/questions/2459295/invertible-stft-and-istft-in-python
    '''

    _log_attributes = ('frame_size', 'hop_size', 'freq_bins')

    def __init__(self, frame_size=0.5, hop_size=0.1, freq_bins=5,
                 spectrogram=False):
        self.frame_size = frame_size
        self.hop_size = hop_size
        self.spectrogram = spectrogram
        self.freq_bins = freq_bins
        super(STFTAudioExtractor, self).__init__()

    def _stft(self, stim):
        x = stim.data
        framesamp = int(self.frame_size*stim.sampling_rate)
        hopsamp = int(self.hop_size*stim.sampling_rate)
        w = np.hanning(framesamp)
        X = np.array([fft(w*x[i:(i+framesamp)])
                      for i in range(0, len(x)-framesamp, hopsamp)])
        nyquist_lim = int(X.shape[1]//2)
        X = np.log(X[:, :nyquist_lim])
        X = np.absolute(X)
        if self.spectrogram:
            import matplotlib.pyplot as plt
            bins = np.fft.fftfreq(framesamp, d=1./stim.sampling_rate)
            bins = bins[:nyquist_lim]
            plt.imshow(X.T, origin='lower', aspect='auto',
                       interpolation='nearest', cmap='RdYlBu_r',
                       extent=[0, stim.duration, bins.min(), bins.max()])
            plt.xlabel('Time')
            plt.ylabel('Frequency')
            plt.colorbar()
            plt.show()
        return X

    def _extract(self, stim):
        data = self._stft(stim)
        time_bins = np.arange(0., stim.duration-self.frame_size, self.hop_size)

        if isinstance(self.freq_bins, int):
            bins = []
            bin_size = data.shape[1] / self.freq_bins
            for i in range(self.freq_bins):
                bins.append((i*bin_size, (i+1)*bin_size))
            self.freq_bins = bins

        features = ['%d_%d' % fb for fb in self.freq_bins]
        offset = 0.0 if stim.onset is None else stim.onset
        index = [tb + offset for tb in time_bins]
        values = np.zeros((len(index), len(features)))
        for i, fb in enumerate(self.freq_bins):
            start, stop = fb
            values[:, i] = data[:, start:stop].mean(1)
        values[np.isnan(values)] = 0.
        values[np.isinf(values)] = 0.
        return ExtractorResult(values, stim, self, features=features,
                               onsets=index, durations=self.hop_size)


class MeanAmplitudeExtractor(Extractor):

    ''' Mean amplitude extractor for blocks of audio with transcription. '''

    _input_type = (AudioStim, ComplexTextStim)

    def _extract(self, stim):

        amps = stim.audio.data
        sampling_rate = stim.audio.sampling_rate
        elements = stim.complex_text.elements
        values, onsets, durations = [], [], []

        for i, el in enumerate(elements):
            onset = sampling_rate * el.onset
            onsets.append(onset)
            duration = sampling_rate * el.duration
            durations.append(duration)

            r_onset = np.round(onset).astype(int)
            r_offset = np.round(onset+duration).astype(int)
            if not r_offset <= amps.shape[0]:
                raise Exception('Block ends after data.')

            mean_amplitude = np.mean(amps[r_onset:r_offset])
            values.append(mean_amplitude)

        return ExtractorResult(values, stim, self, features=['mean_amplitude'],
                               onsets=onsets, durations=durations)


class LibrosaFeatureExtractor(AudioExtractor):

    ''' A generic class for audio extractors using the librosa library. '''

    def __init__(self):
        if librosa is None:
            raise ImportError("librosa is required to create a "
                              "LibrosaFeatureExtractor, but could not be "
                              "successfully imported. Please make sure it is "
                              "installed.")
        super(LibrosaFeatureExtractor, self).__init__()

    @abstractmethod
    def _get_values(self, stim):
        pass

    def _extract(self, stim):
        values, feature_names = self._get_values(stim)
        n_frames = len(values)
        onsets = librosa.frames_to_time(range(n_frames),
                                        sr=stim.sampling_rate,
                                        hop_length=self.hop_length)
        onsets = onsets + stim.onset if stim.onset else onsets
        durations = [self.hop_length / float(stim.sampling_rate)] * n_frames
        return ExtractorResult(values, stim, self,
                               features=feature_names,
                               onsets=onsets,
                               durations=durations)


class SpectralCentroidExtractor(LibrosaFeatureExtractor):

    ''' Extracts the spectral centroids from audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('n_fft', 'hop_length', 'freq')

    def __init__(self, n_fft=2048, hop_length=512, freq=None):
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.freq = freq
        super(SpectralCentroidExtractor, self).__init__()

    def _get_values(self, stim):
        centroids = librosa.feature.spectral_centroid(y=stim.data,
                                                      sr=stim.sampling_rate,
                                                      n_fft=self.n_fft,
                                                      hop_length=self.hop_length,
                                                      freq=self.freq)[0]
        return centroids, ['spectral_centroid']


class SpectralBandwidthExtractor(LibrosaFeatureExtractor):

    ''' Extracts the p'th-order spectral bandwidth from audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('n_fft', 'hop_length', 'freq', 'centroid', 'norm', 'p')

    def __init__(self, n_fft=2048, hop_length=512, freq=None, centroid=None,
                 norm=True, p=2):
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.freq = freq
        self.centroid = centroid
        self.norm = norm
        self.p = p
        super(SpectralBandwidthExtractor, self).__init__()

    def _get_values(self, stim):
        bandwidths = librosa.feature.spectral_bandwidth(y=stim.data,
                                                        sr=stim.sampling_rate,
                                                        n_fft=self.n_fft,
                                                        hop_length=self.hop_length,
                                                        freq=self.freq,
                                                        centroid=self.centroid,
                                                        norm=self.norm,
                                                        p=self.p)[0]
        return bandwidths, ['spectral_bandwidth']


class SpectralContrastExtractor(LibrosaFeatureExtractor):

    ''' Extracts the spectral contrast from audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('n_fft', 'hop_length', 'freq', 'fmin', 'n_bands',
                       'quantile', 'linear')

    def __init__(self, n_fft=2048, hop_length=512, freq=None, fmin=200.0,
                 n_bands=6, quantile=0.02, linear=False):
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.freq = freq
        self.fmin = fmin
        self.n_bands = n_bands
        self.quantile = quantile
        self.linear = linear
        super(SpectralContrastExtractor, self).__init__()

    def _get_values(self, stim):
        contrasts = librosa.feature.spectral_contrast(y=stim.data,
                                                      sr=stim.sampling_rate,
                                                      n_fft=self.n_fft,
                                                      hop_length=self.hop_length,
                                                      freq=self.freq,
                                                      fmin=self.fmin,
                                                      n_bands=self.n_bands,
                                                      quantile=self.quantile,
                                                      linear=self.linear)
        return contrasts.T, ['spectral_contrast_band_%d' % i for i in range(self.n_bands+1)]


class SpectralRolloffExtractor(LibrosaFeatureExtractor):

    ''' Extracts the roll-off frequency from audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('n_fft', 'hop_length', 'freq', 'roll_percent')

    def __init__(self, n_fft=2048, hop_length=512, freq=None,
                 roll_percent=0.85):
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.freq = freq
        self.roll_percent = roll_percent
        super(SpectralRolloffExtractor, self).__init__()

    def _get_values(self, stim):
        rolloffs = librosa.feature.spectral_rolloff(y=stim.data,
                                                    sr=stim.sampling_rate,
                                                    n_fft=self.n_fft,
                                                    hop_length=self.hop_length,
                                                    freq=self.freq,
                                                    roll_percent=self.roll_percent)[0]
        return rolloffs, ['spectral_rolloff']


class PolyFeaturesExtractor(LibrosaFeatureExtractor):

    ''' Extracts the coefficients of fitting an nth-order polynomial to the columns
    of an audio's spectrogram.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('n_fft', 'hop_length', 'order', 'freq')

    def __init__(self, n_fft=2048, hop_length=512, order=1, freq=None):
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.order = order
        self.freq = freq
        super(PolyFeaturesExtractor, self).__init__()

    def _get_values(self, stim):
        poly_features = librosa.feature.poly_features(y=stim.data,
                                                      sr=stim.sampling_rate,
                                                      n_fft=self.n_fft,
                                                      hop_length=self.hop_length,
                                                      order=self.order,
                                                      freq=self.freq)
        return poly_features.T, ['coefficient_%d' % i for i in range(self.order+1)]


class RMSEExtractor(LibrosaFeatureExtractor):

    ''' Extracts root mean square (RMS) energy from audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('frame_length', 'hop_length', 'center', 'pad_mode')

    def __init__(self, frame_length=2048, hop_length=512, center=True,
                 pad_mode='reflect'):
        self.frame_length = frame_length
        self.hop_length = hop_length
        self.center = center
        self.pad_mode = pad_mode
        super(RMSEExtractor, self).__init__()

    def _get_values(self, stim):
        rmse = librosa.feature.rmse(y=stim.data,
                                    frame_length=self.frame_length,
                                    hop_length=self.hop_length,
                                    center=self.center,
                                    pad_mode=self.pad_mode)[0]
        return rmse, ['rmse']


class ZeroCrossingRateExtractor(LibrosaFeatureExtractor):

    ''' Extracts the zero-crossing rate of audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('frame_length', 'hop_length', 'center')

    def __init__(self, frame_length=2048, hop_length=512, center=True,
                 **kwargs):
        self.frame_length = frame_length
        self.hop_length = hop_length
        self.center = center
        self.kwargs = kwargs
        super(ZeroCrossingRateExtractor, self).__init__()

    def _get_values(self, stim):
        zcr = librosa.feature.zero_crossing_rate(stim.data,
                                                 frame_length=self.frame_length,
                                                 hop_length=self.hop_length,
                                                 center=self.center,
                                                 **self.kwargs)[0]
        return zcr, ['zero_crossing_rate']


class ChromaSTFTExtractor(LibrosaFeatureExtractor):

    ''' Extracts a chromagram from an audio's waveform.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('norm', 'n_fft', 'hop_length', 'tuning', 'n_chroma')

    def __init__(self, norm=np.inf, n_fft=2048, hop_length=512, tuning=None,
                 n_chroma=12, **kwargs):
        self.norm = norm
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.tuning = tuning
        self.n_chroma = n_chroma
        self.kwargs = kwargs
        super(ChromaSTFTExtractor, self).__init__()

    def _get_values(self, stim):
        chroma = librosa.feature.chroma_stft(y=stim.data,
                                             sr=stim.sampling_rate,
                                             norm=self.norm,
                                             n_fft=self.n_fft,
                                             hop_length=self.hop_length,
                                             tuning=self.tuning,
                                             n_chroma=self.n_chroma,
                                             **self.kwargs)
        return chroma.T, ['chroma_%d' % i for i in range(self.n_chroma)]


class ChromaCQTExtractor(LibrosaFeatureExtractor):

    ''' Extracts a constant-q chromogram from audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('norm', 'hop_length', 'tuning', 'fmin',
                       'threshold', 'n_chroma', 'n_octaves', 'window',
                       'bins_per_octave', 'cqt_mode')

    def __init__(self, norm=np.inf, hop_length=512, tuning=None,
                 fmin=None, threshold=0.0, n_chroma=12, n_octaves=7,
                 window=None, bins_per_octave=None, cqt_mode='full'):
        self.norm = norm
        self.hop_length = hop_length
        self.tuning = tuning
        self.fmin = fmin
        self.threshold = threshold
        self.n_chroma = n_chroma
        self.n_octaves = n_octaves
        self.window = window
        self.bins_per_octave = bins_per_octave
        self.cqt_mode = cqt_mode
        super(ChromaCQTExtractor, self).__init__()

    def _get_values(self, stim):
        chroma = librosa.feature.chroma_cqt(y=stim.data,
                                            sr=stim.sampling_rate,
                                            norm=self.norm,
                                            hop_length=self.hop_length,
                                            tuning=self.tuning,
                                            fmin=self.fmin,
                                            threshold=self.threshold,
                                            n_chroma=self.n_chroma,
                                            n_octaves=self.n_octaves,
                                            window=self.window,
                                            bins_per_octave=self.bins_per_octave,
                                            cqt_mode=self.cqt_mode)
        return chroma.T, ['chroma_cqt_%d' % i for i in range(self.n_chroma)]


class ChromaCENSExtractor(LibrosaFeatureExtractor):

    ''' Extracts a chroma variant "Chroma Energy Normalized" (CENS)
    chromogram from audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('norm', 'hop_length', 'tuning', 'fmin',
                       'n_chroma', 'n_octaves', 'window', 'bins_per_octave',
                       'cqt_mode', 'norm', 'win_len_smooth')

    def __init__(self, hop_length=512, fmin=None, tuning=None,
                 n_chroma=12, n_octaves=7, window=None, bins_per_octave=None,
                 cqt_mode='full', norm=2, win_len_smooth=41):
        self.hop_length = hop_length
        self.fmin = fmin
        self.tuning = tuning
        self.n_chroma = n_chroma
        self.n_octaves = n_octaves
        self.window = window
        self.bins_per_octave = bins_per_octave
        self.cqt_mode = cqt_mode
        self.norm = norm
        self.win_len_smooth = win_len_smooth
        super(ChromaCENSExtractor, self).__init__()

    def _get_values(self, stim):
        chroma = librosa.feature.chroma_cens(y=stim.data,
                                             sr=stim.sampling_rate,
                                             hop_length=self.hop_length,
                                             fmin=self.fmin,
                                             tuning=self.tuning,
                                             n_chroma=self.n_chroma,
                                             n_octaves=self.n_octaves,
                                             window=self.window,
                                             bins_per_octave=self.bins_per_octave,
                                             cqt_mode=self.cqt_mode,
                                             norm=self.norm,
                                             win_len_smooth=self.win_len_smooth)
        return chroma.T, ['chroma_cens_%d' % i for i in range(self.n_chroma)]


class MelspectrogramExtractor(LibrosaFeatureExtractor):

    ''' Extracts mel-scaled spectrogram from audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('n_mels', 'n_fft', 'hop_length', 'power')

    def __init__(self, n_mels=128, n_fft=2048, hop_length=512, power=2.0,
                 **kwargs):
        self.n_mels = n_mels
        self.n_fft = n_fft
        self.hop_length = hop_length
        self.power = power
        self.kwargs = kwargs
        super(MelspectrogramExtractor, self).__init__()

    def _get_values(self, stim):
        melspectrogram = librosa.feature.melspectrogram(y=stim.data,
                                                        sr=stim.sampling_rate,
                                                        n_mels=self.n_mels,
                                                        n_fft=self.n_fft,
                                                        hop_length=self.hop_length,
                                                        power=self.power,
                                                        **self.kwargs)
        return melspectrogram.T, ['mel_%d' % i for i in range(self.n_mels)]


class MFCCExtractor(LibrosaFeatureExtractor):

    ''' Extracts Mel Frequency Ceptral Coefficients from audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('n_mfcc', 'hop_length')

    def __init__(self, n_mfcc=20, hop_length=512, **kwargs):
        self.n_mfcc = n_mfcc
        self.hop_length = hop_length
        self.kwargs = kwargs
        super(MFCCExtractor, self).__init__()

    def _get_values(self, stim):
        mfcc = librosa.feature.mfcc(y=stim.data,
                                    sr=stim.sampling_rate,
                                    n_mfcc=self.n_mfcc,
                                    hop_length=self.hop_length,
                                    **self.kwargs)
        return mfcc.T, ['mfcc_%d' % i for i in range(self.n_mfcc)]


class TonnetzExtractor(LibrosaFeatureExtractor):

    ''' Extracts the tonal centroids (tonnetz) from audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('chroma',)

    def __init__(self, chroma=None):
        self.chroma = chroma
        self.hop_length = 512
        super(TonnetzExtractor, self).__init__()

    def _get_values(self, stim):
        tonnetz = librosa.feature.tonnetz(y=stim.data,
                                          sr=stim.sampling_rate,
                                          chroma=self.chroma)
        return tonnetz.T, ['tonal_centroid_%d' % i for i in range(6)]


class TempogramExtractor(LibrosaFeatureExtractor):

    ''' Extracts a tempogram from audio.

    For details on argument specification visit:
    https://librosa.github.io/librosa/feature.html.'''

    _log_attributes = ('onset_envelope', 'hop_length', 'win_length', 'center',
                       'window', 'norm')

    def __init__(self, onset_envelope=None, hop_length=512, win_length=384,
                 center=True, window='hann', norm=np.inf):
        self.onset_envelope = onset_envelope
        self.hop_length = hop_length
        self.win_length = win_length
        self.center = center
        self.window = window
        self.norm = norm
        super(TempogramExtractor, self).__init__()

    def _get_values(self, stim):
        tempogram = librosa.feature.tempogram(y=stim.data,
                                              sr=stim.sampling_rate,
                                              onset_envelope=self.onset_envelope,
                                              hop_length=self.hop_length,
                                              win_length=self.win_length,
                                              center=self.center,
                                              window=self.window,
                                              norm=self.norm)
        return tempogram.T, ['tempo_%d' % i for i in range(self.win_length)]
