import os
import time
import numpy as np
from tensorflow.keras.models import load_model
from tensorflow.keras.preprocessing.image import ImageDataGenerator
import h5py
import tensorflow as tf
from tensorflow.keras.preprocessing import image
np.random.seed(123)
from tensorflow.keras import backend as K
from tensorflow.keras.losses import categorical_crossentropy
from tensorflow.keras.metrics import categorical_accuracy
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.utils import to_categorical
from tensorflow.keras.layers import UpSampling2D, Cropping2D
from decimal import Decimal
tf.compat.v1.disable_eager_execution()


##############################
#        PARAMETERS          #
##############################

DEVICE = '1'  # specify which GPU to use

DATA_DIR = 'data'  # data folder
DATA_FILE = 'clean_test_data.h5'  # dataset file
MODEL_DIR = 'models'  # model directory
MODEL_FILENAME = 'sunglasses_bd_net.h5'  # model file
RESULT_DIR = 'results'  # directory for storing results
# image filename template for visualization results
IMG_FILENAME_TEMPLATE = 'visualize_%s_label_%d.png'

# input size
IMG_ROWS = 55
IMG_COLS = 47

IMG_COLOR = 3
INPUT_SHAPE = (IMG_ROWS, IMG_COLS, IMG_COLOR)

NUM_CLASSES = 1283  # total number of classes in the model- N+1
Y_TARGET = 12  # (optional) infected target label, used for prioritizing label scanning

INTENSITY_RANGE = 'raw'  # preprocessing method for the task, GTSRB uses raw pixel intensities #normalize later

# parameters for optimization
BATCH_SIZE = 20  #128  # batch size used for optimization
LR = 0.1  # learning rate
STEPS = 1000  # total optimization iterations
NB_SAMPLE = 1000  # number of samples in each mini batch
MINI_BATCH = NB_SAMPLE // BATCH_SIZE  # mini batch size used for early stop
INIT_COST = 1e-3  # initial weight used for balancing two objectives

REGULARIZATION = 'l1'  # reg term to control the mask's norm

ATTACK_SUCC_THRESHOLD = 0.99  # attack success threshold of the reversed attack
PATIENCE = 5  # patience for adjusting weight, number of mini batches
COST_MULTIPLIER = 2  # multiplier for auto-control of weight (COST)
SAVE_LAST = False  # whether to save the last result or best result

EARLY_STOP = True  # whether to early stop
EARLY_STOP_THRESHOLD = 1.0  # loss threshold for early stop
EARLY_STOP_PATIENCE = 5 * PATIENCE  # patience for early stop

UPSAMPLE_SIZE = 1  # size of the super pixel
MASK_SHAPE = np.ceil(np.array(INPUT_SHAPE[0:2], dtype=float) / UPSAMPLE_SIZE)
MASK_SHAPE = MASK_SHAPE.astype(int)
print(MASK_SHAPE)

# ##############################
# #      END PARAMETERS        #
# ##############################

def dump_image(x, filename, format):            #utils_backdoor_file
    img = image.array_to_img(x, scale=False)
    img.save(filename, format)
    return

def fix_gpu_memory(mem_fraction=1):
    
    with tf.compat.v1.Session() as sess:
        from tensorflow.compat.v1.keras import backend as K
        gpu_options = tf.compat.v1.GPUOptions(per_process_gpu_memory_fraction=mem_fraction)
        tf_config = tf.compat.v1.ConfigProto(gpu_options=gpu_options)
        tf_config.gpu_options.allow_growth = True
        tf_config.log_device_placement = False
        tf_config.allow_soft_placement = True
        init_op = tf.compat.v1.global_variables_initializer()
        sess = tf.compat.v1.Session(config=tf_config)
        sess.run(init_op)
        K.set_session(sess)
        return sess

def load_data(data_filename, keys=None):
    ''' assume all datasets are numpy arrays '''
    dataset = {}
    with h5py.File(data_filename, 'r') as hf:
        if keys is None:
            for name in hf:
                dataset[name] = np.array(hf.get(name))
        else:
            for name in keys:
                dataset[name] = np.array(hf.get(name))

    return dataset

class Visualizer:

    # upsample size, default is 1
    UPSAMPLE_SIZE = 1
    # pixel intensity range of image and preprocessing method
    # raw: [0, 255]
    # mnist: [0, 1]
    # imagenet: imagenet mean centering
    # inception: [-1, 1]
    INTENSITY_RANGE = 'raw'
    # type of regularization of the mask
    REGULARIZATION = 'l1'
    # threshold of attack success rate for dynamically changing cost
    ATTACK_SUCC_THRESHOLD = 0.99
    # patience
    PATIENCE = 10
    # multiple of changing cost, down multiple is the square root of this
    COST_MULTIPLIER = 1.5,
    # if resetting cost to 0 at the beginning
    # default is true for full optimization, set to false for early detection
    RESET_COST_TO_ZERO = True
    # min/max of mask
    MASK_MIN = 0
    MASK_MAX = 1
    # min/max of raw pixel intensity
    COLOR_MIN = 0
    COLOR_MAX = 255
    # number of color channel
    IMG_COLOR = 3
    # whether to shuffle during each epoch
    SHUFFLE = True
    # batch size of optimization
    BATCH_SIZE = 32
    # verbose level, 0, 1 or 2
    VERBOSE = 1
    # whether to return log or not
    RETURN_LOGS = True
    # whether to save last pattern or best pattern
    SAVE_LAST = False
    # epsilon used in tanh
    EPSILON = K.epsilon()
    # early stop flag
    EARLY_STOP = True
    # early stop threshold
    EARLY_STOP_THRESHOLD = 0.99
    # early stop patience
    EARLY_STOP_PATIENCE = 2 * PATIENCE
    # save tmp masks, for debugging purpose
    SAVE_TMP = False
    # dir to save intermediate masks
    TMP_DIR = 'tmp'
    # whether input image has been preprocessed or not
    RAW_INPUT_FLAG = False

    def __init__(self, model, intensity_range, regularization, input_shape,
                 init_cost, steps, mini_batch, lr, num_classes,
                 upsample_size=UPSAMPLE_SIZE,
                 attack_succ_threshold=ATTACK_SUCC_THRESHOLD,
                 patience=PATIENCE, cost_multiplier=COST_MULTIPLIER,
                 reset_cost_to_zero=RESET_COST_TO_ZERO,
                 mask_min=MASK_MIN, mask_max=MASK_MAX,
                 color_min=COLOR_MIN, color_max=COLOR_MAX, img_color=IMG_COLOR,
                 shuffle=SHUFFLE, batch_size=BATCH_SIZE, verbose=VERBOSE,
                 return_logs=RETURN_LOGS, save_last=SAVE_LAST,
                 epsilon=EPSILON,
                 early_stop=EARLY_STOP,
                 early_stop_threshold=EARLY_STOP_THRESHOLD,
                 early_stop_patience=EARLY_STOP_PATIENCE,
                 save_tmp=SAVE_TMP, tmp_dir=TMP_DIR,
                 raw_input_flag=RAW_INPUT_FLAG):

        assert intensity_range in {'imagenet', 'inception', 'mnist', 'raw'}
        assert regularization in {None, 'l1', 'l2'}

        self.model = model
        self.intensity_range = intensity_range
        self.regularization = regularization
        self.input_shape = input_shape
        self.init_cost = init_cost
        self.steps = steps
        self.mini_batch = mini_batch
        self.lr = lr
        self.num_classes = num_classes
        self.upsample_size = upsample_size
        self.attack_succ_threshold = attack_succ_threshold
        self.patience = patience
        self.cost_multiplier_up = cost_multiplier
        self.cost_multiplier_down = cost_multiplier ** 1.5
        self.reset_cost_to_zero = reset_cost_to_zero
        self.mask_min = mask_min
        self.mask_max = mask_max
        self.color_min = color_min
        self.color_max = color_max
        self.img_color = img_color
        self.shuffle = shuffle
        self.batch_size = batch_size
        self.verbose = verbose
        self.return_logs = return_logs
        self.save_last = save_last
        self.epsilon = epsilon
        self.early_stop = early_stop
        self.early_stop_threshold = early_stop_threshold
        self.early_stop_patience = early_stop_patience
        self.save_tmp = save_tmp
        self.tmp_dir = tmp_dir
        self.raw_input_flag = raw_input_flag

        mask_size = np.ceil(np.array(input_shape[0:2], dtype=float) /
                            upsample_size)
        mask_size = mask_size.astype(int)
        self.mask_size = mask_size
        mask = np.zeros(self.mask_size)
        pattern = np.zeros(input_shape)
        mask = np.expand_dims(mask, axis=2)

        mask_tanh = np.zeros_like(mask)
        pattern_tanh = np.zeros_like(pattern)

        # prepare mask related tensors
        self.mask_tanh_tensor = K.variable(mask_tanh)
        mask_tensor_unrepeat = (K.tanh(self.mask_tanh_tensor) /
                                (2 - self.epsilon) +
                                0.5)
        mask_tensor_unexpand = K.repeat_elements(
            mask_tensor_unrepeat,
            rep=self.img_color,
            axis=2)
        self.mask_tensor = K.expand_dims(mask_tensor_unexpand, axis=0)
        upsample_layer = UpSampling2D(
            size=(self.upsample_size, self.upsample_size))
        mask_upsample_tensor_uncrop = upsample_layer(self.mask_tensor)
        uncrop_shape = K.int_shape(mask_upsample_tensor_uncrop)[1:]
        cropping_layer = Cropping2D(
            cropping=((0, uncrop_shape[0] - self.input_shape[0]),
                      (0, uncrop_shape[1] - self.input_shape[1])))
        self.mask_upsample_tensor = cropping_layer(
            mask_upsample_tensor_uncrop)
        reverse_mask_tensor = (K.ones_like(self.mask_upsample_tensor) -
                               self.mask_upsample_tensor)

        def keras_preprocess(x_input, intensity_range):

            if intensity_range is 'raw':
                x_preprocess = x_input

            elif intensity_range is 'imagenet':
                # 'RGB'->'BGR'
                x_tmp = x_input[..., ::-1]
                # Zero-center by mean pixel
                mean = K.constant([[[103.939, 116.779, 123.68]]])
                x_preprocess = x_tmp - mean

            elif intensity_range is 'inception':
                x_preprocess = (x_input / 255.0 - 0.5) * 2.0

            elif intensity_range is 'mnist':
                x_preprocess = x_input / 255.0

            else:
                raise Exception('unknown intensity_range %s' % intensity_range)

            return x_preprocess

        def keras_reverse_preprocess(x_input, intensity_range):

            if intensity_range is 'raw':
                x_reverse = x_input

            elif intensity_range is 'imagenet':
                # Zero-center by mean pixel
                mean = K.constant([[[103.939, 116.779, 123.68]]])
                x_reverse = x_input + mean
                # 'BGR'->'RGB'
                x_reverse = x_reverse[..., ::-1]

            elif intensity_range is 'inception':
                x_reverse = (x_input / 2 + 0.5) * 255.0

            elif intensity_range is 'mnist':
                x_reverse = x_input * 255.0

            else:
                raise Exception('unknown intensity_range %s' % intensity_range)

            return x_reverse

        # prepare pattern related tensors
        self.pattern_tanh_tensor = K.variable(pattern_tanh)
        self.pattern_raw_tensor = (
            (K.tanh(self.pattern_tanh_tensor) / (2 - self.epsilon) + 0.5) *
            255.0)

        # prepare input image related tensors
        # ignore clip operation here
        # assume input image is already clipped into valid color range
        input_tensor = K.placeholder(model.input_shape)
        if self.raw_input_flag:
            input_raw_tensor = input_tensor
        else:
            input_raw_tensor = keras_reverse_preprocess(
                input_tensor, self.intensity_range)

        # IMPORTANT: MASK OPERATION IN RAW DOMAIN
        X_adv_raw_tensor = (
            reverse_mask_tensor * input_raw_tensor +
            self.mask_upsample_tensor * self.pattern_raw_tensor)

        X_adv_tensor = keras_preprocess(X_adv_raw_tensor, self.intensity_range)

        output_tensor = model(X_adv_tensor)
        y_true_tensor = K.placeholder(model.output_shape)

        self.loss_acc = categorical_accuracy(output_tensor, y_true_tensor)

        self.loss_ce = categorical_crossentropy(output_tensor, y_true_tensor)

        if self.regularization is None:
            self.loss_reg = K.constant(0)
        elif self.regularization is 'l1':
            self.loss_reg = (K.sum(K.abs(self.mask_upsample_tensor)) /
                             self.img_color)
        elif self.regularization is 'l2':
            self.loss_reg = K.sqrt(K.sum(K.square(self.mask_upsample_tensor)) /
                                   self.img_color)

        cost = self.init_cost
        self.cost_tensor = K.variable(cost)
        self.loss = self.loss_ce + self.loss_reg * self.cost_tensor

        self.opt = Adam(lr=self.lr, beta_1=0.5, beta_2=0.9)
        self.updates = self.opt.get_updates(
            params=[self.pattern_tanh_tensor, self.mask_tanh_tensor],
            loss=self.loss)
        self.train = K.function(
            [input_tensor, y_true_tensor],
            [self.loss_ce, self.loss_reg, self.loss, self.loss_acc],
            updates=self.updates)

        pass

    def reset_opt(self):

        K.set_value(self.opt.iterations, 0)
        for w in self.opt.weights:
            K.set_value(w, np.zeros(K.int_shape(w)))

        pass

    def reset_state(self, pattern_init, mask_init):

        print('resetting state')

        # setting cost
        if self.reset_cost_to_zero:
            self.cost = 0
        else:
            self.cost = self.init_cost
        K.set_value(self.cost_tensor, self.cost)

        # setting mask and pattern
        mask = np.array(mask_init)
        pattern = np.array(pattern_init)
        mask = np.clip(mask, self.mask_min, self.mask_max)
        pattern = np.clip(pattern, self.color_min, self.color_max)
        mask = np.expand_dims(mask, axis=2)

        # convert to tanh space
        mask_tanh = np.arctanh((mask - 0.5) * (2 - self.epsilon))
        pattern_tanh = np.arctanh((pattern / 255.0 - 0.5) * (2 - self.epsilon))
        print('mask_tanh', np.min(mask_tanh), np.max(mask_tanh))
        print('pattern_tanh', np.min(pattern_tanh), np.max(pattern_tanh))

        K.set_value(self.mask_tanh_tensor, mask_tanh)
        K.set_value(self.pattern_tanh_tensor, pattern_tanh)

        # resetting optimizer states
        self.reset_opt()

        pass

    def save_tmp_func(self, step):

        cur_mask = K.eval(self.mask_upsample_tensor)
        cur_mask = cur_mask[0, ..., 0]
        img_filename = (
            '%s/%s' % (self.tmp_dir, 'tmp_mask_step_%d.png' % step))
        dump_image(np.expand_dims(cur_mask, axis=2) * 255,
                                  img_filename,
                                  'png')

        cur_fusion = K.eval(self.mask_upsample_tensor *
                            self.pattern_raw_tensor)
        cur_fusion = cur_fusion[0, ...]
        img_filename = (
            '%s/%s' % (self.tmp_dir, 'tmp_fusion_step_%d.png' % step))
        dump_image(cur_fusion, img_filename, 'png')

        pass

    def visualize(self, gen, y_target, pattern_init, mask_init):

        # since we use a single optimizer repeatedly, we need to reset
        # optimzier's internal states before running the optimization
        self.reset_state(pattern_init, mask_init)

        # best optimization results
        mask_best = None
        mask_upsample_best = None
        pattern_best = None
        reg_best = float('inf')

        # logs and counters for adjusting balance cost
        logs = []
        cost_set_counter = 0
        cost_up_counter = 0
        cost_down_counter = 0
        cost_up_flag = False
        cost_down_flag = False

        # counter for early stop
        early_stop_counter = 0
        early_stop_reg_best = reg_best

        # vectorized target
        Y_target = to_categorical([y_target] * self.batch_size,
                                  self.num_classes)

        # loop start
        for step in range(self.steps):

            # record loss for all mini-batches
            loss_ce_list = []
            loss_reg_list = []
            loss_list = []
            loss_acc_list = []
            for idx in range(self.mini_batch):
                X_batch, _ = gen.next()
                if X_batch.shape[0] != Y_target.shape[0]:
                    Y_target = to_categorical([y_target] * X_batch.shape[0],
                                              self.num_classes)
                (loss_ce_value,
                    loss_reg_value,
                    loss_value,
                    loss_acc_value) = self.train([X_batch, Y_target])
                loss_ce_list.extend(list(loss_ce_value.flatten()))
                loss_reg_list.extend(list(loss_reg_value.flatten()))
                loss_list.extend(list(loss_value.flatten()))
                loss_acc_list.extend(list(loss_acc_value.flatten()))

            avg_loss_ce = np.mean(loss_ce_list)
            avg_loss_reg = np.mean(loss_reg_list)
            avg_loss = np.mean(loss_list)
            avg_loss_acc = np.mean(loss_acc_list)

            # check to save best mask or not
            if avg_loss_acc >= self.attack_succ_threshold and avg_loss_reg < reg_best:
                mask_best = K.eval(self.mask_tensor)
                mask_best = mask_best[0, ..., 0]
                mask_upsample_best = K.eval(self.mask_upsample_tensor)
                mask_upsample_best = mask_upsample_best[0, ..., 0]
                pattern_best = K.eval(self.pattern_raw_tensor)
                reg_best = avg_loss_reg

            # verbose
            if self.verbose != 0:
                if self.verbose == 2 or step % (self.steps // 10) == 0:
                    print('step: %3d, cost: %.2E, attack: %.3f, loss: %f, ce: %f, reg: %f, reg_best: %f' %
                          (step, Decimal(self.cost), avg_loss_acc, avg_loss,
                           avg_loss_ce, avg_loss_reg, reg_best))

            # save log
            logs.append((step,
                         avg_loss_ce, avg_loss_reg, avg_loss, avg_loss_acc,
                         reg_best, self.cost))

            # check early stop
            if self.early_stop:
                # only terminate if a valid attack has been found
                if reg_best < float('inf'):
                    if reg_best >= self.early_stop_threshold * early_stop_reg_best:
                        early_stop_counter += 1
                    else:
                        early_stop_counter = 0
                early_stop_reg_best = min(reg_best, early_stop_reg_best)

                if (cost_down_flag and
                        cost_up_flag and
                        early_stop_counter >= self.early_stop_patience):
                    print('early stop')
                    break

            # check cost modification
            if self.cost == 0 and avg_loss_acc >= self.attack_succ_threshold:
                cost_set_counter += 1
                if cost_set_counter >= self.patience:
                    self.cost = self.init_cost
                    K.set_value(self.cost_tensor, self.cost)
                    cost_up_counter = 0
                    cost_down_counter = 0
                    cost_up_flag = False
                    cost_down_flag = False
                    print('initialize cost to %.2E' % Decimal(self.cost))
            else:
                cost_set_counter = 0

            if avg_loss_acc >= self.attack_succ_threshold:
                cost_up_counter += 1
                cost_down_counter = 0
            else:
                cost_up_counter = 0
                cost_down_counter += 1

            if cost_up_counter >= self.patience:
                cost_up_counter = 0
                if self.verbose == 2:
                    print('up cost from %.2E to %.2E' %
                          (Decimal(self.cost),
                           Decimal(self.cost * self.cost_multiplier_up)))
                self.cost *= self.cost_multiplier_up
                K.set_value(self.cost_tensor, self.cost)
                cost_up_flag = True
            elif cost_down_counter >= self.patience:
                cost_down_counter = 0
                if self.verbose == 2:
                    print('down cost from %.2E to %.2E' %
                          (Decimal(self.cost),
                           Decimal(self.cost / self.cost_multiplier_down)))
                self.cost /= self.cost_multiplier_down
                K.set_value(self.cost_tensor, self.cost)
                cost_down_flag = True

            if self.save_tmp:
                self.save_tmp_func(step)

        # save the final version
        if mask_best is None or self.save_last:
            mask_best = K.eval(self.mask_tensor)
            mask_best = mask_best[0, ..., 0]
            mask_upsample_best = K.eval(self.mask_upsample_tensor)
            mask_upsample_best = mask_upsample_best[0, ..., 0]
            pattern_best = K.eval(self.pattern_raw_tensor)

        if self.return_logs:
            return pattern_best, mask_best, mask_upsample_best, logs
        else:
            return pattern_best, mask_best, mask_upsample_best

def load_dataset(data_file=('%s' % (DATA_FILE))): # Load the

    dataset = load_data(data_file, keys=['data', 'label'])

    X_test = np.array(dataset['data'], dtype='float32')
    Y_test = np.array(dataset['label'], dtype='float32')

    print('X_test shape %s' % str(X_test.shape))
    print('Y_test shape %s' % str(Y_test.shape))

    return X_test, Y_test

def build_data_loader(X, Y):

    datagen = ImageDataGenerator() #Generate batches of tensor image data with real-time data augmentation.
    generator = datagen.flow(
        X, Y, batch_size=BATCH_SIZE)

    return generator

def visualize_trigger_w_mask(visualizer, gen, y_target,save_pattern_flag=True):  #actual reverse engineered trigger

    visualize_start_time = time.time()

    # initialize with random mask
    pattern = np.random.random(INPUT_SHAPE) * 255.0
    mask = np.random.random(MASK_SHAPE)

    # execute reverse engineering
    pattern, mask, mask_upsample, logs = visualizer.visualize(gen=gen, y_target=y_target, pattern_init=pattern, mask_init=mask)

    # meta data about the generated mask
    print('pattern, shape: %s, min: %f, max: %f' %
          (str(pattern.shape), np.min(pattern), np.max(pattern)))
    print('mask, shape: %s, min: %f, max: %f' %
          (str(mask.shape), np.min(mask), np.max(mask)))
    print('mask norm of label %d: %f' %
          (y_target, np.sum(np.abs(mask_upsample))))

    visualize_end_time = time.time()
    print('visualization cost %f seconds' %
          (visualize_end_time - visualize_start_time))

    if save_pattern_flag:
        save_pattern(pattern, mask_upsample, y_target)

    return pattern, mask_upsample, logs

def save_pattern(pattern, mask, y_target): #find and save pattern detected

    # create result dir
    if not os.path.exists(RESULT_DIR):
        os.mkdir(RESULT_DIR)

    img_filename = (
        '%s/%s' % (RESULT_DIR,
                   IMG_FILENAME_TEMPLATE % ('pattern', y_target)))
    dump_image(pattern, img_filename, 'png')

    img_filename = (
        '%s/%s' % (RESULT_DIR,
                   IMG_FILENAME_TEMPLATE % ('mask', y_target)))
    dump_image(np.expand_dims(mask, axis=2) * 255,img_filename,'png')

    fusion = np.multiply(pattern, np.expand_dims(mask, axis=2))
    img_filename = (
        '%s/%s' % (RESULT_DIR,
                   IMG_FILENAME_TEMPLATE % ('fusion', y_target)))
    dump_image(fusion, img_filename, 'png')

    pass

def start_analyse():

    print('loading dataset')
    X_test, Y_test = load_dataset()
    X_test = np.transpose(X_test, (0 , 2, 3, 1))
    #data, label = load_dataset()
    # transform numpy arrays into data generator
    test_generator = build_data_loader(X_test,Y_test)

    print('loading model')
    model_file = '%s' % (MODEL_FILENAME)
    print(model_file)
    model = load_model(model_file)

    # initialize visualizer
    visualizer = Visualizer(
        model, intensity_range=INTENSITY_RANGE, regularization=REGULARIZATION,
        input_shape=INPUT_SHAPE,
        init_cost=INIT_COST, steps=STEPS, lr=LR, num_classes=NUM_CLASSES,
        mini_batch=MINI_BATCH,
        upsample_size=UPSAMPLE_SIZE,
        attack_succ_threshold=ATTACK_SUCC_THRESHOLD,
        patience=PATIENCE, cost_multiplier=COST_MULTIPLIER,
        img_color=IMG_COLOR, batch_size=BATCH_SIZE, verbose=2,
        save_last=SAVE_LAST,
        early_stop=EARLY_STOP, early_stop_threshold=EARLY_STOP_THRESHOLD,
        early_stop_patience=EARLY_STOP_PATIENCE)

    log_mapping = {}

    # y_label list to analyze
    y_target_list = list(range(NUM_CLASSES))
    y_target_list.remove(Y_TARGET)
    y_target_list = [Y_TARGET] + y_target_list
    for y_target in y_target_list:

        print('processing label %d' % y_target)

        _, _, logs = visualize_trigger_w_mask(
            visualizer, test_generator, y_target=y_target,
            save_pattern_flag=True)

        log_mapping[y_target] = logs

    pass

start_time = time.time()
os.environ["CUDA_VISIBLE_DEVICES"] = DEVICE
fix_gpu_memory()
start_analyse()
elapsed_time = time.time() - start_time
print('elapsed time %s s' % elapsed_time)

